"""Shared launch flow for investigation-style tools.

``investigation_start`` (free-text) and ``alert_sample`` (template) share the
same shape: gate through the execution policy, announce, run in the background or
foreground, and record the outcome. This helper holds that flow once; each tool
supplies only the parts that differ (the run callable, the background launcher,
and the display/record strings).
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from platform.common.task_types import TaskRecord
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.ui.foreground_investigation import run_foreground_investigation
from tools.interactive_shell.shared.execution_policy import plan_foreground_tool


def launch_investigation(
    *,
    session: Session,
    console: Console,
    tool_type: str,
    action_summary: str,
    announce_label: str,
    announce_value: str,
    record_value: str,
    foreground_task_command: str,
    exception_context: str,
    run: Callable[[TaskRecord], dict[str, object]],
    start_background: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    """Gate, announce, and run an investigation-style tool, recording the outcome.

    Every outcome is recorded on the ``alert`` channel keyed by ``record_value``.
    """
    plan = plan_foreground_tool(tool_type, "investigation_launch")
    if not execution_allowed(
        plan.policy,
        session=session,
        console=console,
        action_summary=action_summary,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("alert", record_value, ok=False)
        return

    console.print(f"[bold]{announce_label}:[/bold] {escape(announce_value)}")

    if session.background_mode_enabled:
        start_background()
        session.record("alert", record_value)
        return

    if (
        run_foreground_investigation(
            session=session,
            console=console,
            task_command=foreground_task_command,
            run=run,
            exception_context=exception_context,
            target=record_value,
        ).status
        != "completed"
    ):
        session.record("alert", record_value, ok=False)
        return

    session.record("alert", record_value)


__all__ = ["launch_investigation"]
