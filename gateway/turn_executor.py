"""Execute one gateway turn through the interactive-shell harness."""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

from rich.console import Console

from core.agent_harness.session import ReplSession
from core.agent_harness.turn_results import ShellTurnResult
from core.execution import ToolExecutionHooks
from gateway.approvals.telegram import TelegramApprovalService, inject_gateway_chat_context
from gateway.config import GatewaySettings
from gateway.platforms.telegram.client import TelegramBotClient
from gateway.sinks.telegram_sink import TelegramOutputSink
from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

logger = logging.getLogger(__name__)


def execute_gateway_turn(
    *,
    text: str,
    session: ReplSession,
    client: TelegramBotClient,
    chat_id: str,
    settings: GatewaySettings,
    approval_service: TelegramApprovalService,
    confirm_fn: Callable[[str], str] | None = None,
) -> ShellTurnResult:
    """Run a full shell turn and stream the answer back to Telegram."""
    sink = TelegramOutputSink(
        client=client,
        chat_id=chat_id,
        edit_interval_seconds=settings.stream_edit_interval_seconds,
    )
    console_buffer = io.StringIO()
    console = Console(
        file=console_buffer,
        highlight=False,
        force_terminal=False,
    )
    hooks: ToolExecutionHooks = approval_service.hooks()

    # Warm integrations before injecting gateway metadata; an empty cache with
    # only _gateway_* keys would otherwise block the gather loop and action tools.
    session.warm_resolved_integrations()
    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
    )

    effective_confirm = confirm_fn
    if effective_confirm is None:

        def effective_confirm(prompt: str) -> str:
            return approval_service.wait_for_confirmation(chat_id=chat_id, prompt=prompt)

    result = execute_shell_turn(
        text,
        session,
        console,
        recorder=None,
        confirm_fn=effective_confirm,
        is_tty=False,
        output=sink,
        tool_hooks=hooks,
    )

    if not result.answered:
        reply_text = (
            result.assistant_response_text.strip()
            or result.action_result.response_text.strip()
            or console_buffer.getvalue().strip()
        )
        if reply_text:
            sink.finalize(reply_text)

    logger.info(
        "[gateway] turn complete answered=%s intent=%s",
        result.answered,
        result.final_intent,
    )
    return result
