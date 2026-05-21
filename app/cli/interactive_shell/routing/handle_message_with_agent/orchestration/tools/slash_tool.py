"""Slash command action tool."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from app.cli.interactive_shell.command_registry.slash_catalog import (
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)
from app.cli.interactive_shell.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    execution_allowed,
    plan_slash_execution,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
)


def execute_slash_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    command = str(args.get("command", "")).strip()
    raw_args = args.get("args")
    parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
    full_command = " ".join([command, *parsed_args]) if parsed_args else command
    stripped = full_command.strip()
    if stripped == "/" or not stripped:
        return bool(
            dispatch_slash(
                stripped or "/",
                ctx.session,
                ctx.console,
                confirm_fn=ctx.confirm_fn,
                is_tty=ctx.is_tty,
            )
        )

    parts = stripped.split()
    name = parts[0].lower()
    slash_args = parts[1:]
    cmd = SLASH_COMMANDS.get(name)
    if cmd is None:
        return bool(
            dispatch_slash(
                stripped,
                ctx.session,
                ctx.console,
                confirm_fn=ctx.confirm_fn,
                is_tty=ctx.is_tty,
            )
        )

    plan = plan_slash_execution(name, slash_args, cmd.execution_tier)
    if not execution_allowed(
        plan.policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=stripped,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        ctx.session.record("slash", stripped, ok=False)
        return True

    ctx.console.print(f"[bold]$ {escape(stripped)}[/bold]")
    return bool(
        dispatch_slash(
            stripped,
            ctx.session,
            ctx.console,
            confirm_fn=ctx.confirm_fn,
            is_tty=ctx.is_tty,
            policy_precleared=True,
        )
    )


TOOL_ENTRY = ToolEntry(
    name="slash_invoke",
    description=slash_invoke_tool_description(),
    input_schema=slash_invoke_input_schema(),
    execution_tier=ExecutionTier.SAFE,
    execute=execute_slash_action,
    is_available=lambda session: capability_not_explicitly_disabled(session, "slash_commands"),
)


__all__ = ["TOOL_ENTRY", "execute_slash_action"]
