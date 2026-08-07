"""Handlers for polled inbound Telegram messages."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from config.scope_context import bound_storage_scope
from gateway.core.runtime.active_turns import USER_STOP_MESSAGE, ActiveTurnCancels
from gateway.core.runtime.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.core.storage import SessionResolver
from gateway.transports.telegram.approvals import TelegramApprovalPrompter
from gateway.transports.telegram.inbound_security import (
    enforce_inbound_telegram_message_security,
)
from gateway.transports.telegram.output_sink import GatewayOutputSink
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.principal import (
    PrincipalResolutionError,
    resolve_telegram_scope,
)
from gateway.transports.telegram.session_rotation import resolve_or_rotate_session
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage
from platform.analytics.usage_context import SURFACE_TELEGRAM, bound_usage_context

logger = logging.getLogger(__name__)

_TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."


async def handle_polled_inbound_telegram_message(
    event: TelegramInboundMessage,
    *,
    client: TelegramBotClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    approvals: ApprovalBroker,
    active_cancels: ActiveTurnCancels,
    loop: asyncio.AbstractEventLoop | None = None,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Process one long-polled inbound Telegram update."""
    user_lock = chat_locks.setdefault(event.user_id, asyncio.Lock())
    decision = enforce_inbound_telegram_message_security(
        user_id=event.user_id,
        chat_id=event.chat_id,
        text=event.text,
        env_allowed_user_ids=settings.allowed_user_ids,
    )
    try:
        scope = resolve_telegram_scope(user_id=event.user_id)
    except PrincipalResolutionError:
        logger.error(
            "[telegram-gateway] turn refused: unresolved principal user=%s chat=%s",
            event.user_id,
            event.chat_id,
            exc_info=True,
        )
        return

    async with user_lock, turn_semaphore:
        with bound_storage_scope(scope):
            session = resolve_or_rotate_session(
                event,
                decision,
                session_resolver=session_resolver,
                client=client,
                scope=scope,
            )
            if session is None:
                return

            preview = event.text.replace("\n", " ").strip()
            if len(preview) > 80:
                preview = f"{preview[:77]}..."
            logger.info(
                "inbound user=%s chat=%s session=%s principal=%s text=%r",
                event.user_id,
                event.chat_id,
                session.session_id[:8],
                scope.principal.id,
                preview,
            )

            sink = GatewayOutputSink(
                client=client,
                chat_id=event.chat_id,
                edit_interval_seconds=settings.stream_edit_interval_seconds,
                tool_hooks=approval_tool_hooks(
                    TelegramApprovalPrompter(
                        client=client,
                        broker=approvals,
                        chat_id=event.chat_id,
                    )
                ),
            )

            event_loop = loop or asyncio.get_running_loop()
            outcome_lock = threading.Lock()
            outcome_taken = False

            def _claim_terminal_outcome() -> bool:
                # First of {timeout, error, normal completion} owns the final
                # message. Soft timeout also sets ``sink.turn_cancel`` so the
                # ReAct loop stops cooperatively; the executor thread is not killed.
                nonlocal outcome_taken
                with outcome_lock:
                    if outcome_taken:
                        return False
                    outcome_taken = True
                    return True

            turn_cancel = threading.Event()
            sink.turn_cancel = turn_cancel

            def _on_turn_timeout() -> None:
                # Signal cooperative cancel so the ReAct loop / tools stop; then
                # claim the sink terminal message for the timeout UX copy.
                turn_cancel.set()
                if not _claim_terminal_outcome():
                    return
                logger.warning(
                    "[telegram-gateway] turn TIMED OUT after %.0fs chat=%s session=%s",
                    settings.turn_timeout_seconds,
                    event.chat_id,
                    session.session_id[:8],
                )
                try:
                    sink.finalize(_TURN_TIMEOUT_MESSAGE)
                except Exception:
                    logger.debug(
                        "[telegram-gateway] timeout finalize failed",
                        exc_info=True,
                    )

            def _on_user_stop() -> None:
                if not _claim_terminal_outcome():
                    return
                try:
                    sink.finalize(USER_STOP_MESSAGE)
                except Exception:
                    logger.debug("[telegram-gateway] user-stop finalize failed", exc_info=True)

            def _run_turn() -> None:
                with (
                    bound_storage_scope(scope),
                    bound_usage_context(
                        surface=SURFACE_TELEGRAM,
                        session_id=session.session_id,
                        user_id=event.user_id or None,
                    ),
                ):
                    handle_callback_to_gateway_agent(
                        event.text,
                        session,
                        sink,
                        logger,
                    )

            timer = threading.Timer(settings.turn_timeout_seconds, _on_turn_timeout)
            timer.start()
            try:
                with active_cancels.track(event.chat_id, turn_cancel, on_user_stop=_on_user_stop):
                    await event_loop.run_in_executor(executor, _run_turn)
            except Exception:
                logger.exception(
                    "[telegram-gateway] turn ERRORED chat=%s session=%s",
                    event.chat_id,
                    session.session_id[:8],
                )
                if _claim_terminal_outcome():
                    try:
                        sink.render_error("Something went wrong on that request.")
                    except Exception:
                        logger.debug(
                            "[telegram-gateway] error finalize failed",
                            exc_info=True,
                        )
                raise
            finally:
                timer.cancel()
            if _claim_terminal_outcome():
                logger.info(
                    "[telegram-gateway] turn done chat=%s session=%s",
                    event.chat_id,
                    session.session_id[:8],
                )
