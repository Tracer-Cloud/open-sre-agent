"""Background Slack Socket Mode gateway service: connection + lifecycle."""

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

from gateway.runtime.errors import GatewayConfigurationError
from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.slack.approvals import ApprovalBroker, handle_block_actions_payload
from gateway.slack.channel_intro import ChannelIntroGreeter
from gateway.slack.client import SlackWebApiClient
from gateway.slack.dispatcher import _SlackTurnDispatcher
from gateway.slack.events import parse_events_api_payload
from gateway.slack.feedback import record_feedback_payload
from gateway.slack.settings import SlackGatewaySettings
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db

_PLATFORM_SLACK = "slack"
_EVENTS_API_REQUEST_TYPE = "events_api"
_INTERACTIVE_REQUEST_TYPE = "interactive"


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
        """Disconnect from Slack, wait up to ``timeout`` for in-flight turns, and clean up."""
        try:
            self._socket_client.close()
        except Exception:
            logging.getLogger(__name__).debug("[slack-gateway] close failed", exc_info=True)
        # shutdown() has no timeout parameter, so bound the wait with a joiner thread.
        waiter = threading.Thread(
            target=lambda: self._executor.shutdown(wait=True, cancel_futures=False),
            name="SlackGatewayShutdown",
            daemon=True,
        )
        waiter.start()
        waiter.join(timeout)
        stopped = not waiter.is_alive()
        try:
            self._db.close()
        except Exception:
            logging.getLogger(__name__).debug("[slack-gateway] db close failed", exc_info=True)
        return stopped


def _resolve_bot_user_id(web_client: WebClient, logger: logging.Logger) -> str:
    """Return the bot's own Slack user id via auth.test, or '' on failure."""
    try:
        return str(web_client.auth_test().get("user_id") or "")
    except Exception:
        logger.debug("[slack-gateway] auth.test for bot_user_id failed", exc_info=True)
        return ""


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
    # Resolve the bot's own user id once so thread seeding can label the bot's
    # replies by author, not by fragile text-shape matching.
    bot_user_id = _resolve_bot_user_id(web_client, logger)
    approvals = ApprovalBroker()
    messaging = SlackWebApiClient(web_client)
    greeter = ChannelIntroGreeter(messaging=messaging, bot_user_id=bot_user_id)
    dispatcher = _SlackTurnDispatcher(
        settings=settings,
        messaging=messaging,
        session_resolver=SessionResolver(SessionBindingStore(db), platform=_PLATFORM_SLACK),
        handler=handler,
        logger=logger,
        bot_user_id=bot_user_id,
        approvals=approvals,
    )

    def _on_request(client: BaseSocketModeClient, request: SocketModeRequest) -> None:
        # Ack first: Slack redelivers any envelope not acked within 3 seconds.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type == _INTERACTIVE_REQUEST_TYPE:
            # Approval clicks resolve on the listener thread: turn workers may
            # all be blocked *waiting* on these buttons, so a click must never
            # need a free worker. Feedback clicks share the envelope type.
            record_feedback_payload(request.payload)
            handle_block_actions_payload(
                request.payload,
                broker=approvals,
                allowed_user_ids=settings.allowed_user_ids,
                allow_open_workspace=settings.allow_open_workspace,
            )
            return
        if request.type != _EVENTS_API_REQUEST_TYPE:
            return
        event_type = str((request.payload.get("event") or {}).get("type") or "")
        if event_type == "member_joined_channel":
            # Greeting posts a message (network call): hand it to a worker.
            executor.submit(greeter.handle, request.payload)
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
