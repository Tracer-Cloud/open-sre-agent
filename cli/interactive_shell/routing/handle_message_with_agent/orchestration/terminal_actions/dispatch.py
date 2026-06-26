"""Dispatch planned terminal actions through the tool registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
    ToolContext,
)
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.ui import DIM, print_planned_actions
from cli.interactive_shell.ui.streaming import render_response_header


def tool_args_for_action(action: PlannedAction) -> dict[str, Any]:
    if action.args:
        return dict(action.args)
    content = action.content.strip()
    if action.kind == "slash":
        parts = content.split()
        return {
            "command": parts[0] if parts else "",
            "args": parts[1:] if len(parts) > 1 else [],
        }
    if action.kind == "llm_provider":
        return {"provider": content}
    if action.kind == "shell":
        return {"command": content}
    if action.kind == "sample_alert":
        return {"template": content}
    if action.kind == "investigation":
        return {"alert_text": content}
    if action.kind == "synthetic_test":
        suite, _sep, scenario = content.partition(":")
        return {"suite": suite, "scenario": scenario}
    if action.kind == "task_cancel":
        return {"target": content}
    if action.kind == "cli_command":
        return {"payload": content}
    if action.kind == "implementation":
        return {"task": content}
    return {"content": content}


def execute_planned_actions(
    *,
    actions: list[PlannedAction],
    has_unhandled_clause: bool,
    message: str,
    session: ReplSession,
    console: Console,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    dispatch_fn: Callable[..., bool] | None = None,
) -> bool:
    console.print()
    render_response_header(console, "assistant")
    print_planned_actions(console, actions)
    if not has_unhandled_clause:
        session.record("cli_agent", message)

    for action in actions:
        if getattr(console, "cancel_requested", False):
            console.print(f"[{DIM}](remaining actions cancelled)[/]")
            break
        console.print()
        tool_name = ACTION_KIND_TO_TOOL.get(action.kind)
        if tool_name is None:
            continue

        args = tool_args_for_action(action)
        ctx = ToolContext(
            session=session,
            console=console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            action_already_listed=True,
        )
        if dispatch_fn is None:
            REGISTRY.dispatch(tool_name=tool_name, args=args, ctx=ctx)
        else:
            dispatch_fn(tool_name=tool_name, args=args, ctx=ctx)

    console.print()
    return not has_unhandled_clause


__all__ = ["execute_planned_actions", "tool_args_for_action"]
