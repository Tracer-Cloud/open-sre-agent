"""Telegram message action tool."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.contracts import ToolContext, ToolEntry
from tools.interactive_shell.shared import allow_tool
from tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool
from tools.telegram_send_message_tool import telegram_send_message

_REGISTERED_TOOL = getattr(telegram_send_message, REGISTERED_TOOL_ATTR)
if not isinstance(_REGISTERED_TOOL, RegisteredTool):  # pragma: no cover - import-time contract
    raise TypeError("telegram_send_message must expose a RegisteredTool")


def _telegram_configured(session: object) -> bool:
    """True when the shell knows Telegram is configured for this session."""
    resolved = getattr(session, "resolved_integrations_cache", None)
    if isinstance(resolved, dict):
        telegram = resolved.get("telegram")
        if isinstance(telegram, dict) and telegram.get("bot_token"):
            return True

    configured_known = bool(getattr(session, "configured_integrations_known", False))
    configured = getattr(session, "configured_integrations", ())
    return configured_known and "telegram" in configured


def _run_telegram_send_message(
    *,
    message: str,
    chat_id: str,
    reply_to_message_id: str,
) -> dict[str, Any]:
    result = _REGISTERED_TOOL.run(
        message=message,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
    )
    if isinstance(result, dict):
        return result
    return {"sent": False, "error": f"unexpected result from telegram_send_message: {result!r}"}


def execute_telegram_send_message_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
    message = str(args.get("message", "")).strip()
    if not message:
        return False

    chat_id = str(args.get("chat_id", "")).strip()
    reply_to_message_id = str(args.get("reply_to_message_id", "")).strip()
    policy = allow_tool("telegram_send_message")
    if not execution_allowed(
        policy,
        session=ctx.session,
        console=ctx.console,
        action_summary="telegram_send_message",
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        ctx.session.record("telegram_send_message", message, ok=False)
        return True

    ctx.console.print("[bold]$ telegram_send_message[/bold]")
    result = _run_telegram_send_message(
        message=message,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
    )
    sent = bool(result.get("sent"))
    if sent:
        chat = str(result.get("chat_id") or chat_id or "configured default chat")
        ctx.console.print(f"[green]telegram message sent[/] [dim]({escape(chat)})[/]")
    else:
        error = str(result.get("error") or "unknown delivery error")
        ctx.console.print(f"[red]telegram message failed:[/] {escape(error)}")
    ctx.session.record("telegram_send_message", message, ok=sent)
    return True


TOOL_ENTRY = ToolEntry(
    name=_REGISTERED_TOOL.name,
    description=(
        "Send a plain-text Telegram message when the user explicitly asks to send, "
        "post, notify, or message a Telegram chat. This is an external side effect."
    ),
    input_schema=_REGISTERED_TOOL.public_input_schema,
    execute=execute_telegram_send_message_tool,
    is_available=_telegram_configured,
)


__all__ = ["TOOL_ENTRY", "execute_telegram_send_message_tool"]
