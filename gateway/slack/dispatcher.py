"""Per-turn Slack dispatch: admit gate, auth, thread seeding, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from core.agent_harness.session import SessionCore
from gateway.billing.credits_client import CreditsOutcome, consume_credits
from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.slack.approvals import (
    ApprovalBroker,
    ThreadApprovalPrompter,
    approval_tool_hooks,
)
from gateway.slack.attention import GateDecision, ThreadAttentionGate
from gateway.slack.client import (
    SlackMessagingClient,
    mark_turn_done,
    mark_turn_failed,
    mark_turn_working,
)
from gateway.slack.events import SlackInboundFile, SlackInboundMessage
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
from gateway.storage import SessionResolver

_ROTATE_SESSION = "__ROTATE_SESSION__"

# Per-thread locks are pruned once this many conversations have been seen,
# keeping memory flat in workspaces where every message starts a new thread.
_MAX_CONVERSATION_LOCKS = 1024

_DENIAL_REPLY = "You're not authorized to use this bot. Ask an admin to add you."
_NEW_SESSION_REPLY = "Started a new session."
_TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
# Only an explicit 402 from the credit ledger posts this; UNCONFIGURED /
# UNAVAILABLE outcomes run the turn instead, so a misconfiguration or webapp
# outage never masquerades to users as "out of credits".
_CREDITS_DENIED_MESSAGE = "Out of credits — top up in the OpenSRE console."


@dataclass
class _ConversationLock:
    """A per-conversation lock with a holder/waiter count for safe pruning."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    refs: int = 0


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
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self._settings = settings
        self._messaging = messaging
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._bot_user_id = bot_user_id
        self._approvals = approvals if approvals is not None else ApprovalBroker()
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

            # Metering: only an explicit webapp denial (402) blocks the turn,
            # so a config error can never masquerade to users as "out of
            # credits". UNCONFIGURED (dev setups without metering env) and
            # UNAVAILABLE (webapp outage) proceed — fail-open is the intended
            # policy so a billing outage never silences the Slack coworker.
            if consume_credits(reason="slack_turn") is CreditsOutcome.DENIED:
                self._logger.info(
                    "[slack-gateway] turn denied: out of credits channel=%s",
                    inbound.channel_id,
                )
                self._post(inbound, _CREDITS_DENIED_MESSAGE)
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
            # Write tools declaring requires_approval get an Approve/Deny
            # button prompt in this thread before they run (fail-closed).
            prompter = ThreadApprovalPrompter(
                client=self._messaging,
                broker=self._approvals,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
            )
            sink = SlackOutputSink(
                client=self._messaging,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
                update_interval_seconds=self._settings.status_update_interval_seconds,
                tool_hooks=approval_tool_hooks(prompter),
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
                if inbound.files and (
                    files_context := _slack_files_context(inbound.files, self._logger)
                ):
                    agent_text = f"{agent_text}\n\n{files_context}"
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


def _slack_files_context(files: tuple[SlackInboundFile, ...], logger: logging.Logger) -> str:
    """Download shared files and render them as text for the turn prompt.

    Returns an empty string when the bot token is unavailable (metering-style
    fail-safe — a missing token drops attachments rather than failing the turn).
    """
    from gateway.slack.attachments import build_files_context
    from integrations.slack.web_client import resolve_bot_token

    target, detail = resolve_bot_token()
    if target is None:
        logger.info("[slack-gateway] skipping %d file(s): %s", len(files), detail)
        return ""
    return build_files_context(files, target.bot_token)


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
