"""Investigation tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from interactive_shell.runtime import ReplSession
from platform.common.task_types import TaskRecord
from tools.interactive_shell.contracts import (
    ToolContext,
    ToolEntry,
    object_schema,
    string_property,
)
from tools.interactive_shell.shared.investigation_launch import launch_investigation


def run_text_investigation(
    alert_text: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    def _run(task: TaskRecord) -> dict[str, object]:
        from cli.investigation import run_investigation_for_session

        return run_investigation_for_session(
            alert_text=alert_text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )

    def _start_background() -> None:
        from interactive_shell.runtime.background.runner import (
            start_background_text_investigation,
        )

        start_background_text_investigation(
            alert_text=alert_text,
            session=session,
            console=console,
            display_command="background free-text investigation",
        )

    launch_investigation(
        session=session,
        console=console,
        tool_type="investigation",
        action_summary=f'investigation from text "{alert_text}"',
        announce_label="investigation",
        announce_value=alert_text,
        record_value=alert_text,
        foreground_task_command=f"investigate:{alert_text}",
        exception_context="interactive_shell.text_investigation",
        run=_run,
        start_background=_start_background,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


def execute_investigation_tool(args: dict[str, Any], ctx: ToolContext) -> bool:
    alert_text = str(args.get("alert_text", "")).strip()
    if not alert_text:
        return False
    run_text_investigation(
        alert_text,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


TOOL_ENTRY = ToolEntry(
    name="investigation_start",
    description=(
        "Start an investigation with the provided alert text or quoted payload. "
        "Use whenever the user explicitly instructs you to investigate, RCA, "
        "diagnose, analyze, root-cause, or send an investigation payload — including "
        "'investigate why X ...' and placeholder quoted text like 'hello world' — "
        "regardless of CONNECTED INTEGRATIONS. In compound turns like `run /remote "
        'and then investigate "hello world"`, emit this as a separate second tool '
        "call; never drop the quoted investigation after emitting the slash command. "
        "Do NOT use for bare incident statements with no investigate verb, generic "
        "'Run an investigation.' with no subject, sample/demo alerts, or plain data "
        "lookups."
    ),
    input_schema=object_schema(
        properties={
            "alert_text": string_property(
                description="Alert text or incident details to investigate.",
                min_length=1,
            )
        },
        required=("alert_text",),
    ),
    execute=execute_investigation_tool,
)


__all__ = ["TOOL_ENTRY", "execute_investigation_tool", "run_text_investigation"]
