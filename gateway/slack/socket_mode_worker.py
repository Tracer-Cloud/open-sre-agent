"""Background Slack Socket Mode gateway service."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web import WebClient

from core.agent_harness.session import SessionCore
from gateway.runtime.errors import GatewayConfigurationError
from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.slack.attention import GateDecision, ThreadAttentionGate
from gateway.slack.client import (
    SlackMessagingClient,
    SlackWebApiClient,
    mark_turn_done,
    mark_turn_failed,
    mark_turn_working,
)
from gateway.slack.events import SlackInboundMessage, parse_events_api_payload
from gateway.slack.output_sink import SlackOutputSink
from gateway.slack.security import (
    SlackInboundDecision,
    enforce_inbound_slack_message_security,
    persist_policy_if_needed,
)
from gateway.slack.settings import SlackGatewaySettings
from gateway.slack.thread_history import (
    seed_session_from_slack_thread,
    session_needs_thread_seed,
)
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db

_PLATFORM_SLACK = "slack"
_EVENTS_API_REQUEST_TYPE = "events_api"
_ROTATE_SESSION = "__ROTATE_SESSION__"

# Per-thread locks are pruned once this many conversations have been seen,
# keeping memory flat in workspaces where every message starts a new thread.
_MAX_CONVERSATION_LOCKS = 1024

_DENIAL_REPLY = "You're not authorized to use this bot. Ask an admin to add you."
_NEW_SESSION_REPLY = "Started a new session."
_TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."


@dataclass
class _ConversationLock:
    """A per-conversation lock with a holder/waiter count for safe pruning."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    refs: int = 0


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
        bot_user_id: str = "",
    ) -> None:
        self._settings = settings
        self._messaging = messaging
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._bot_user_id = bot_user_id
        self._attention = ThreadAttentionGate()
        self._conversation_locks: dict[str, _ConversationLock] = {}
        self._locks_guard = threading.Lock()
        self._resolver_lock = threading.Lock()

    def dispatch(self, inbound: SlackInboundMessage) -> None:
        try:
            if not self._admit(inbound):
                return
            self._run_turn(inbound)
        except Exception:
            self._logger.error("[slack-gateway] turn failed", exc_info=True)

    def _admit(self, inbound: SlackInboundMessage) -> bool:
        """Layered gate: decide whether this inbound message runs a turn at all.

        Mentions and DMs always run (and open/refresh the thread's attention
        window). An un-tagged thread reply runs only when every free check
        passes: the bot already joined the thread (bindings store), the
        attention window from the last mention is still open, and the reply is
        for the bot — either the thread is a 1:1 conversation with the bot
        (one human speaker → every reply engages, DM-style) or the
        deterministic address check matches. Human-to-human traffic in
        multi-user threads passes through silently.
        """
        if inbound.addressed:
            self._attention.note_addressed_turn(inbound.conversation_key, user_id=inbound.user_id)
            return True
        # Layer 1: only threads the bot already joined; never channel chatter.
        if not self._session_resolver.has_session(user_id=inbound.conversation_key):
            return False
        if not self._bot_user_id:
            # Without our own id we can neither dedup mention copies nor run
            # the address check safely: require explicit mentions.
            return False
        if f"<@{self._bot_user_id}>" in inbound.text:
            # When both app_mention and message.channels are subscribed, a
            # mention arrives twice; drop the plain-message copy.
            return False
        # Layers 2-3: attention window + address check + unprompted rate limit.
        # 1:1 threads (one human + the bot) engage every reply, DM-style.
        decision = self._attention.decide(
            conversation_key=inbound.conversation_key,
            text=inbound.text,
            user_id=inbound.user_id,
            bot_user_id=self._bot_user_id,
        )
        if decision is GateDecision.RATE_LIMITED:
            # Heard, but over the unprompted budget: acknowledge, don't reply.
            self._messaging.add_reaction(
                channel=inbound.channel_id, timestamp=inbound.ts, emoji="eyes"
            )
            self._logger.info(
                "[slack-gateway] unprompted reply rate-limited channel=%s thread_ts=%s",
                inbound.channel_id,
                inbound.thread_ts,
            )
            return False
        if decision is not GateDecision.ENGAGE:
            return False
        self._logger.info(
            "[slack-gateway] engaging un-tagged thread reply channel=%s thread_ts=%s",
            inbound.channel_id,
            inbound.thread_ts,
        )
        return True

    @contextmanager
    def _conversation_turn(self, conversation_key: str) -> Iterator[None]:
        """Serialize turns per conversation, pruning idle lock entries at the cap.

        The reference count marks an entry as in use from before this thread
        leaves the guard until after it releases the lock, so pruning can never
        discard a lock another thread is about to acquire.
        """
        with self._locks_guard:
            entry = self._conversation_locks.get(conversation_key)
            if entry is None:
                if len(self._conversation_locks) >= _MAX_CONVERSATION_LOCKS:
                    self._conversation_locks = {
                        key: existing
                        for key, existing in self._conversation_locks.items()
                        if existing.refs > 0
                    }
                entry = self._conversation_locks[conversation_key] = _ConversationLock()
            entry.refs += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._locks_guard:
                entry.refs -= 1

    def _post(self, inbound: SlackInboundMessage, text: str) -> None:
        self._messaging.post_message(
            channel=inbound.channel_id,
            text=text,
            thread_ts=inbound.thread_ts,
        )

    def _apply_inbound_decision(
        self,
        inbound: SlackInboundMessage,
        decision: SlackInboundDecision,
    ) -> SessionCore | None:
        """Apply auth decision side effects. Return a session to run, or None to stop."""
        persist_policy_if_needed(decision)

        if not inbound.addressed and (not decision.allowed or decision.reply_text):
            # An un-tagged reply the bot chose to answer must never turn into
            # denial/help/pairing chatter in a human conversation: anything but
            # a clean authorized turn stays silent. Commands require a mention.
            return None

        is_rotate = decision.reply_text == _ROTATE_SESSION
        if decision.reply_text and not is_rotate:
            # Pairing / help replies are safe to show; never echo allowlist
            # denial reasons (those stay in the audit log only).
            self._post(inbound, decision.reply_text)
            if not decision.allowed:
                return None

        if not decision.allowed and not is_rotate:
            self._post(inbound, _DENIAL_REPLY)
            return None

        with self._resolver_lock:
            if is_rotate:
                session = self._session_resolver.rotate(
                    user_id=inbound.conversation_key,
                    chat_id=inbound.channel_id,
                )
                self._post(inbound, _NEW_SESSION_REPLY)
                if inbound.text.strip().lower() == "/new":
                    return None
                return session
            return self._session_resolver.resolve(
                user_id=inbound.conversation_key,
                chat_id=inbound.channel_id,
            )

    def _run_turn(self, inbound: SlackInboundMessage) -> None:
        with self._conversation_turn(inbound.conversation_key):
            decision = enforce_inbound_slack_message_security(
                user_id=inbound.user_id,
                channel_id=inbound.channel_id,
                text=inbound.text,
                env_allowed_user_ids=self._settings.allowed_user_ids,
                allow_open_workspace=self._settings.allow_open_workspace,
            )
            session = self._apply_inbound_decision(inbound, decision)
            if session is None:
                return

            # Never log message bodies — audit hashes live in messaging_security.
            # ts vs thread_ts distinguishes a new mention (ts == thread_ts) from a
            # threaded reply — key to diagnosing session continuity.
            is_reply = inbound.thread_ts != inbound.ts
            self._logger.info(
                "inbound platform=slack user=%s channel=%s thread_ts=%s reply=%s "
                "session=%s chars=%d",
                inbound.user_id,
                inbound.channel_id,
                inbound.thread_ts,
                is_reply,
                session.session_id[:8],
                len(inbound.text),
            )
            # Continuity + availability diagnostics: prior-message count shows
            # whether "yes"-style follow-ups kept context; the slack flag shows
            # whether the Slack teammate tools will be offered this turn.
            resolved = getattr(session, "resolved_integrations_cache", None) or {}
            prior_msgs = len(getattr(session, "cli_agent_messages", []) or [])
            self._logger.info(
                "turn setup platform=slack prior_msgs=%d slack_resolved=%s",
                prior_msgs,
                "slack" in resolved,
            )
            turn_started = time.monotonic()
            mark_turn_working(
                self._messaging,
                channel=inbound.channel_id,
                timestamp=inbound.ts,
            )
            sink = SlackOutputSink(
                client=self._messaging,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
                update_interval_seconds=self._settings.status_update_interval_seconds,
            )
            outcome_lock = threading.Lock()
            outcome_taken = False

            def _claim_terminal_outcome() -> bool:
                # The first of {timeout, error, normal completion} to claim owns
                # the final message + reaction. This keeps a timed-out turn that
                # later finishes from stacking a done tick over the timeout's
                # cross, and stops a timeout racing an error from finalizing twice.
                nonlocal outcome_taken
                with outcome_lock:
                    if outcome_taken:
                        return False
                    outcome_taken = True
                    return True

            def _on_turn_timeout() -> None:
                # A blocking handler cannot be cancelled, so surface a visible
                # message and mark the turn failed instead of leaving a frozen
                # placeholder; the orphaned turn keeps running.
                if not _claim_terminal_outcome():
                    return
                self._logger.warning(
                    "[slack-gateway] turn TIMED OUT after %.0fs channel=%s session=%s",
                    self._settings.turn_timeout_seconds,
                    inbound.channel_id,
                    session.session_id[:8],
                )
                try:
                    sink.finalize(_TURN_TIMEOUT_MESSAGE)
                except Exception:
                    self._logger.debug("[slack-gateway] timeout finalize failed", exc_info=True)
                mark_turn_failed(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )

            timer = threading.Timer(self._settings.turn_timeout_seconds, _on_turn_timeout)
            timer.start()
            try:
                # Slack thread is the continuity source when the
                # gateway session file is empty (redeploy / ephemeral disk).
                if session_needs_thread_seed(inbound.text, is_reply=is_reply):
                    seeded = seed_session_from_slack_thread(
                        session,
                        channel_id=inbound.channel_id,
                        thread_ts=inbound.thread_ts,
                        exclude_ts=inbound.ts,
                        bot_user_id=self._bot_user_id,
                    )
                    if seeded:
                        self._logger.info(
                            "seeded session history from Slack thread msgs=%d",
                            seeded,
                        )
                agent_text = _agent_text_with_slack_context(inbound)
                self._handler(agent_text, session, sink, self._logger)
            except Exception:
                self._logger.exception(
                    "[slack-gateway] turn ERRORED after %.1fs channel=%s session=%s",
                    time.monotonic() - turn_started,
                    inbound.channel_id,
                    session.session_id[:8],
                )
                # Replace the "Digging in…" placeholder with a visible error —
                # otherwise a raised turn is indistinguishable from one still
                # running (only the ✗ reaction changes). Skip if the timeout
                # already owns the outcome.
                if _claim_terminal_outcome():
                    try:
                        sink.render_error("Something went wrong on that request.")
                    except Exception:
                        self._logger.debug("[slack-gateway] error finalize failed", exc_info=True)
                    mark_turn_failed(
                        self._messaging,
                        channel=inbound.channel_id,
                        timestamp=inbound.ts,
                    )
                raise
            finally:
                timer.cancel()
            if _claim_terminal_outcome():
                self._logger.info(
                    "[slack-gateway] turn done in %.1fs channel=%s session=%s",
                    time.monotonic() - turn_started,
                    inbound.channel_id,
                    session.session_id[:8],
                )
                mark_turn_done(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )


def _agent_text_with_slack_context(inbound: SlackInboundMessage) -> str:
    """Prefix inbound text with the channel id + speaker for teammate targeting.

    Short metadata line only — tool routing lives in action prompts. The thread
    ts is omitted so the agent does not copy it into channel reads (which would
    return one thread instead of channel history); the reply sink and session
    seeding already target the triggering thread. The speaker is included as a
    Slack mention token so multi-user threads stay attributable ("what is my
    name?" must resolve to the asker, not whoever spoke earlier); echoed back
    it renders as @name in Slack.
    """
    speaker = f" user=<@{inbound.user_id}>" if inbound.user_id else ""
    return f"[Slack channel_id={inbound.channel_id}{speaker}]\n{inbound.text}"


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
    dispatcher = _SlackTurnDispatcher(
        settings=settings,
        messaging=SlackWebApiClient(web_client),
        session_resolver=SessionResolver(SessionBindingStore(db), platform=_PLATFORM_SLACK),
        handler=handler,
        logger=logger,
        bot_user_id=bot_user_id,
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
