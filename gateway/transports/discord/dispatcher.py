"""Per-turn Discord dispatch: admit gate, auth, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session import SessionCore
from gateway.core.billing.credits_client import CreditsOutcome, consume_credits
from gateway.core.runtime.active_turns import (
    USER_STOP_MESSAGE,
    ActiveTurnCancels,
    is_stop_command,
)
from gateway.core.runtime.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.runtime.attention import GateDecision, ThreadAttentionGate
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.core.storage import SessionResolver
from gateway.transports.discord.approvals import DiscordApprovalPrompter
from gateway.transports.discord.client import add_reaction, remove_reaction, send_message
from gateway.transports.discord.events import DiscordInboundMessage
from gateway.transports.discord.output_sink import DiscordOutputSink
from gateway.transports.discord.principal import PrincipalResolutionError, resolve_discord_scope
from gateway.transports.discord.security import (
    _ROTATE_SESSION,
    DiscordInboundDecision,
    enforce_inbound_discord_message_security,
    persist_policy_if_needed,
)
from gateway.transports.discord.settings import DiscordGatewaySettings
from gateway.transports.discord.thread_history import (
    seed_session_from_discord_thread,
    session_needs_thread_seed,
)
from platform.analytics.usage_context import SURFACE_DISCORD, bound_usage_context

_MAX_CONVERSATION_LOCKS = 1024
_DENIAL_REPLY = "You're not authorized to use this bot. Ask an admin to add you."
_NEW_SESSION_REPLY = "Started a new session."
_TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
_CREDITS_DENIED_MESSAGE = "Out of credits — top up in the OpenSRE console."
# Discord's reaction API takes the literal Unicode emoji (URL-encoded), not a name.
_WORKING_EMOJI = "\N{EYES}"
_DONE_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
_FAILED_EMOJI = "\N{CROSS MARK}"


@dataclass
class _ConversationLock:
    lock: threading.Lock = field(default_factory=threading.Lock)
    refs: int = 0


class DiscordTurnDispatcher:
    """Runs authorized inbound Discord messages through the gateway agent callback."""

    def __init__(
        self,
        *,
        settings: DiscordGatewaySettings,
        bot_token: str,
        session_resolver: SessionResolver,
        handler: GatewayAgentCallback,
        logger: logging.Logger,
        bot_user_id: str = "",
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self._settings = settings
        self._bot_token = bot_token
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._bot_user_id = bot_user_id
        self._approvals = approvals or ApprovalBroker()
        self._active_cancels = ActiveTurnCancels()
        self._attention = ThreadAttentionGate()
        self._conversation_locks: dict[str, _ConversationLock] = {}
        self._locks_guard = threading.Lock()
        self._resolver_lock = threading.Lock()

    @property
    def bot_user_id(self) -> str:
        return self._bot_user_id

    def set_bot_user_id(self, bot_user_id: str) -> None:
        self._bot_user_id = bot_user_id

    def dispatch(self, inbound: DiscordInboundMessage) -> None:
        # /stop must not wait on the per-conversation turn lock.
        if is_stop_command(inbound.text):
            if not self._active_cancels.request_stop(inbound.conversation_key):
                send_message(
                    channel_id=inbound.channel_id,
                    content="Nothing running to stop.",
                    bot_token=self._bot_token,
                )
            return
        try:
            scope = resolve_discord_scope(guild_id=inbound.guild_id, user_id=inbound.user_id)
        except PrincipalResolutionError:
            self._logger.error(
                "[discord-gateway] turn refused: unresolved principal channel=%s",
                inbound.channel_id,
                exc_info=True,
            )
            return
        try:
            with bound_storage_scope(scope):
                if not self._admit(inbound, scope):
                    return
                self._run_turn(inbound, scope)
        except Exception:
            self._logger.error("[discord-gateway] turn failed", exc_info=True)

    def _admit(self, inbound: DiscordInboundMessage, scope: StorageScope) -> bool:
        if inbound.addressed:
            self._attention.note_addressed_turn(inbound.conversation_key, user_id=inbound.user_id)
            return True
        if not self._session_resolver.has_conversation(
            conversation_key=inbound.conversation_key,
            principal=scope.principal,
        ):
            return False
        if not self._bot_user_id:
            return False
        decision = self._attention.decide(
            conversation_key=inbound.conversation_key,
            text=inbound.text,
            user_id=inbound.user_id,
            bot_user_id=self._bot_user_id,
        )
        if decision is GateDecision.RATE_LIMITED:
            add_reaction(
                channel_id=inbound.channel_id,
                message_id=inbound.message_id,
                emoji=_WORKING_EMOJI,
                bot_token=self._bot_token,
            )
            return False
        if decision is not GateDecision.ENGAGE:
            return False
        self._logger.info(
            "[discord-gateway] engaging un-tagged thread reply channel=%s thread=%s",
            inbound.channel_id,
            inbound.thread_id,
        )
        return True

    @contextmanager
    def _conversation_turn(self, conversation_key: str) -> Iterator[None]:
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

    def _post(self, inbound: DiscordInboundMessage, text: str) -> None:
        from gateway.transports.discord.client import send_message

        send_message(
            channel_id=inbound.channel_id,
            content=text,
            bot_token=self._bot_token,
        )

    def _apply_inbound_decision(
        self,
        inbound: DiscordInboundMessage,
        decision: DiscordInboundDecision,
        scope: StorageScope,
    ) -> SessionCore | None:
        persist_policy_if_needed(decision)

        if not inbound.addressed and (not decision.allowed or decision.reply_text):
            return None

        is_rotate = decision.reply_text == _ROTATE_SESSION
        if decision.reply_text and not is_rotate:
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
                    principal=scope.principal,
                    actor=scope.actor,
                )
                self._post(inbound, _NEW_SESSION_REPLY)
                if inbound.text.strip().lower() == "/new":
                    return None
                return session
            return self._session_resolver.resolve(
                user_id=inbound.conversation_key,
                chat_id=inbound.channel_id,
                principal=scope.principal,
                actor=scope.actor,
            )

    def _run_turn(self, inbound: DiscordInboundMessage, scope: StorageScope) -> None:
        with self._conversation_turn(inbound.conversation_key):
            decision = enforce_inbound_discord_message_security(
                user_id=inbound.user_id,
                channel_id=inbound.channel_id,
                text=inbound.text,
                env_allowed_user_ids=self._settings.allowed_user_ids,
                allow_open_guild=self._settings.allow_open_guild,
                is_guild_message=inbound.is_guild_message,
            )
            session = self._apply_inbound_decision(inbound, decision, scope)
            if session is None:
                return

            if consume_credits(scope.principal.id, reason="discord_turn") is CreditsOutcome.DENIED:
                self._logger.info(
                    "[discord-gateway] turn denied: out of credits channel=%s",
                    inbound.channel_id,
                )
                self._post(inbound, _CREDITS_DENIED_MESSAGE)
                return

            is_reply = not inbound.addressed
            self._logger.info(
                "inbound platform=discord user=%s channel=%s thread=%s reply=%s "
                "session=%s chars=%d",
                inbound.user_id,
                inbound.channel_id,
                inbound.thread_id,
                is_reply,
                session.session_id[:8],
                len(inbound.text),
            )

            add_reaction(
                channel_id=inbound.channel_id,
                message_id=inbound.message_id,
                emoji=_WORKING_EMOJI,
                bot_token=self._bot_token,
            )
            sink = DiscordOutputSink(
                bot_token=self._bot_token,
                channel_id=inbound.channel_id,
                edit_interval_seconds=self._settings.status_update_interval_seconds,
                tool_hooks=approval_tool_hooks(
                    DiscordApprovalPrompter(
                        broker=self._approvals,
                        bot_token=self._bot_token,
                        channel_id=inbound.channel_id,
                    )
                ),
            )
            outcome_lock = threading.Lock()
            outcome_taken = False

            def _claim_terminal_outcome() -> bool:
                nonlocal outcome_taken
                with outcome_lock:
                    if outcome_taken:
                        return False
                    outcome_taken = True
                    return True

            turn_cancel = threading.Event()
            sink.turn_cancel = turn_cancel

            def _on_turn_timeout() -> None:
                turn_cancel.set()
                if not _claim_terminal_outcome():
                    return
                try:
                    sink.finalize(_TURN_TIMEOUT_MESSAGE)
                except Exception:
                    self._logger.debug("[discord-gateway] timeout finalize failed", exc_info=True)
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_FAILED_EMOJI,
                    bot_token=self._bot_token,
                )

            def _on_user_stop() -> None:
                if not _claim_terminal_outcome():
                    return
                try:
                    sink.finalize(USER_STOP_MESSAGE)
                except Exception:
                    self._logger.debug("[discord-gateway] user-stop finalize failed", exc_info=True)
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_FAILED_EMOJI,
                    bot_token=self._bot_token,
                )

            timer = threading.Timer(self._settings.turn_timeout_seconds, _on_turn_timeout)
            timer.start()
            turn_started = time.monotonic()
            try:
                if session_needs_thread_seed(inbound.text, is_reply=is_reply):
                    seed_session_from_discord_thread(
                        session,
                        history=list(inbound.thread_history),
                    )
                agent_text = inbound.text
                if inbound.attachments:
                    from gateway.transports.discord.attachments import (
                        build_discord_attachments_context,
                    )

                    ctx = build_discord_attachments_context(
                        inbound.attachments,
                        bot_token=self._bot_token,
                    )
                    if ctx:
                        agent_text = f"{agent_text}\n\n{ctx}"
                with (
                    self._active_cancels.track(
                        inbound.conversation_key,
                        turn_cancel,
                        on_user_stop=_on_user_stop,
                    ),
                    bound_usage_context(
                        surface=SURFACE_DISCORD,
                        session_id=session.session_id,
                        user_id=inbound.user_id or None,
                    ),
                ):
                    self._handler(agent_text, session, sink, self._logger)
            except Exception:
                if _claim_terminal_outcome():
                    with suppress(Exception):
                        sink.render_error("Something went wrong on that request.")
                    remove_reaction(
                        channel_id=inbound.channel_id,
                        message_id=inbound.message_id,
                        emoji=_WORKING_EMOJI,
                        bot_token=self._bot_token,
                    )
                    add_reaction(
                        channel_id=inbound.channel_id,
                        message_id=inbound.message_id,
                        emoji=_FAILED_EMOJI,
                        bot_token=self._bot_token,
                    )
                raise
            finally:
                timer.cancel()
            if _claim_terminal_outcome():
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_DONE_EMOJI,
                    bot_token=self._bot_token,
                )
                self._logger.info(
                    "[discord-gateway] turn done in %.1fs channel=%s session=%s",
                    time.monotonic() - turn_started,
                    inbound.channel_id,
                    session.session_id[:8],
                )
