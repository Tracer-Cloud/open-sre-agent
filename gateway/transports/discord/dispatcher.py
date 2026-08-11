"""Per-turn Discord dispatch: admit gate, auth, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import suppress

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    NEW_SESSION_MESSAGE,
    NO_ACTIVE_TURN_MESSAGE,
    TURN_ERROR_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    USER_STOP_MESSAGE,
)
from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session import SessionCore
from gateway.core.billing.credits_client import CreditsOutcome, consume_credits
from gateway.core.runtime.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.runtime.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.runtime.attention import GateDecision, ThreadAttentionGate
from gateway.core.runtime.conversation_locks import ConversationLockRegistry
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.core.runtime.terminal_outcome import TerminalOutcomeArbiter
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

# Discord's reaction API takes the literal Unicode emoji (URL-encoded), not a name.
_WORKING_EMOJI = "\N{EYES}"
_DONE_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
_FAILED_EMOJI = "\N{CROSS MARK}"


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
        self._active_cancels = ActiveTurnRegistry()
        self._attention = ThreadAttentionGate()
        self._conversation_locks = ConversationLockRegistry()
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
                    content=NO_ACTIVE_TURN_MESSAGE,
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
            self._post(inbound, UNAUTHORIZED_MESSAGE)
            return None

        with self._resolver_lock:
            if is_rotate:
                session = self._session_resolver.rotate(
                    user_id=inbound.conversation_key,
                    chat_id=inbound.channel_id,
                    principal=scope.principal,
                    actor=scope.actor,
                )
                self._post(inbound, NEW_SESSION_MESSAGE)
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
        with self._conversation_locks.hold(inbound.conversation_key):
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
                self._post(inbound, CREDITS_DENIED_MESSAGE)
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
            terminal = TerminalOutcomeArbiter()
            sink.turn_cancel = terminal.cancel_event

            def _on_turn_timeout() -> None:
                try:
                    sink.finalize(TURN_TIMEOUT_MESSAGE)
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
                if not terminal.claim():
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

            turn_started = time.monotonic()
            with terminal.timeout_after(self._settings.turn_timeout_seconds, _on_turn_timeout):
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
                            terminal.cancel_event,
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
                    if terminal.claim():
                        with suppress(Exception):
                            sink.render_error(TURN_ERROR_MESSAGE)
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
            if terminal.claim():
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
