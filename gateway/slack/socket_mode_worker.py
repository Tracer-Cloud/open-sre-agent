"""Background Slack Socket Mode gateway service."""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web import WebClient

from gateway.config.get_gateway_settings import GatewayConfigurationError
from gateway.sink_protocol import GatewayAgentCallback
from gateway.slack.client import SlackMessagingClient, SlackWebApiClient
from gateway.slack.events import SlackInboundMessage, parse_events_api_payload
from gateway.slack.output_sink import SlackOutputSink
from gateway.slack.security import authorize_slack_message
from gateway.slack.settings import SlackGatewaySettings
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db

_PLATFORM_SLACK = "slack"
_EVENTS_API_REQUEST_TYPE = "events_api"


class SlackGatewayBackground:
    """Control handle for the background Slack Socket Mode worker."""

    def __init__(
        self,
        *,
        socket_client: SocketModeClient,
        executor: ThreadPoolExecutor,
        db: sqlite3.Connection,
    ) -> None:
        self._socket_client = socket_client
        self._executor = executor
        self._db = db

    def stop(self, *, timeout: float = 8.0) -> bool:
        """Disconnect from Slack and release worker resources."""
        _ = timeout
        try:
            self._socket_client.close()
        except Exception:
            logging.getLogger(__name__).debug("[slack-gateway] close failed", exc_info=True)
        self._executor.shutdown(wait=False, cancel_futures=False)
        try:
            self._db.close()
        except Exception:
            logging.getLogger(__name__).debug("[slack-gateway] db close failed", exc_info=True)
        return True


class _SlackTurnDispatcher:
    """Runs authorized inbound Slack messages through the gateway agent callback."""

    def __init__(
        self,
        *,
        settings: SlackGatewaySettings,
        messaging: SlackMessagingClient,
        session_resolver: SessionResolver,
        handler: GatewayAgentCallback,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._messaging = messaging
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._resolver_lock = threading.Lock()

    def dispatch(self, inbound: SlackInboundMessage) -> None:
        try:
            self._run_turn(inbound)
        except Exception:
            self._logger.error("[slack-gateway] turn failed", exc_info=True)

    def _run_turn(self, inbound: SlackInboundMessage) -> None:
        lock = self._conversation_locks.setdefault(inbound.conversation_key, threading.Lock())
        with lock:
            result = authorize_slack_message(
                user_id=inbound.user_id,
                channel_id=inbound.channel_id,
                text=inbound.text,
                allowed_user_ids=self._settings.allowed_user_ids,
                allow_open_workspace=self._settings.allow_open_workspace,
            )
            if not result:
                self._messaging.post_message(
                    channel=inbound.channel_id,
                    text=result.reason,
                    thread_ts=inbound.thread_ts,
                )
                return

            with self._resolver_lock:
                session = self._session_resolver.resolve(
                    user_id=inbound.conversation_key,
                    chat_id=inbound.channel_id,
                )
            # Never log message bodies — audit hashes live in messaging_security.
            self._logger.info(
                "inbound platform=slack user=%s channel=%s session=%s chars=%d",
                inbound.user_id,
                inbound.channel_id,
                session.session_id[:8],
                len(inbound.text),
            )
            sink = SlackOutputSink(
                client=self._messaging,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
                update_interval_seconds=self._settings.status_update_interval_seconds,
            )
            self._handler(inbound.text, session, sink, self._logger)


def start_slack_gateway_background(
    *,
    settings: SlackGatewaySettings,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> SlackGatewayBackground:
    """Connect to Slack over Socket Mode and dispatch inbound messages until stopped."""
    web_client = WebClient(token=settings.bot_token)
    socket_client = SocketModeClient(app_token=settings.app_token, web_client=web_client)
    db = connect_gateway_db()
    executor = ThreadPoolExecutor(
        max_workers=settings.max_concurrent_turns,
        thread_name_prefix="SlackGatewayTurn",
    )
    dispatcher = _SlackTurnDispatcher(
        settings=settings,
        messaging=SlackWebApiClient(web_client),
        session_resolver=SessionResolver(SessionBindingStore(db), platform=_PLATFORM_SLACK),
        handler=handler,
        logger=logger,
    )

    def _on_request(client: BaseSocketModeClient, request: SocketModeRequest) -> None:
        # Ack first: Slack redelivers any envelope not acked within 3 seconds.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type != _EVENTS_API_REQUEST_TYPE:
            return
        inbound = parse_events_api_payload(request.payload)
        if inbound is None:
            return
        executor.submit(dispatcher.dispatch, inbound)

    socket_client.socket_mode_request_listeners.append(_on_request)
    try:
        socket_client.connect()
    except Exception as exc:
        executor.shutdown(wait=False)
        db.close()
        raise GatewayConfigurationError(f"Slack Socket Mode connect failed: {exc}") from exc

    logger.info("[slack-gateway] socket mode connected")
    return SlackGatewayBackground(socket_client=socket_client, executor=executor, db=db)
