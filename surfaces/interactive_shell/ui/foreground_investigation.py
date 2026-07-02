"""Shared foreground investigation task lifecycle for REPL entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from platform.common.errors import OpenSREError
from platform.common.task_types import TaskKind, TaskRecord
from platform.terminal.theme import ERROR, WARNING
from surfaces.interactive_shell.ui.investigation_outcome import (
    InvestigationOutcome,
    classify_investigation_failure,
    failure_detail_from_exception,
    normalize_investigation_target,
    user_facing_error_message,
)
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception

if TYPE_CHECKING:
    from core.agent_harness.session import Session


def run_foreground_investigation(
    *,
    session: Session,
    console: Console,
    task_command: str,
    run: Callable[[TaskRecord], dict[str, Any]],
    exception_context: str,
    target: str = "",
) -> InvestigationOutcome:
    """Run one foreground investigation with shared task and error handling."""
    normalized_target = normalize_investigation_target(target)
    session.last_investigation_id = ""
    task = session.task_registry.create(TaskKind.INVESTIGATION, command=task_command)
    task.mark_running()
    try:
        final_state = run(task)
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        return InvestigationOutcome(
            status="cancelled",
            target=normalized_target,
            investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
            failure_category="user_cancelled",
        )
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        category, integration, integration_detail = classify_investigation_failure(exc)
        return InvestigationOutcome(
            status="failed",
            target=normalized_target,
            investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
            error_message=user_facing_error_message(exc),
            error_detail=failure_detail_from_exception(exc),
            failure_category=category,
            integration_involved=integration,
            integration_failure_message=integration_detail,
        )
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context=exception_context)
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        category, integration, integration_detail = classify_investigation_failure(exc)
        return InvestigationOutcome(
            status="failed",
            target=normalized_target,
            investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
            error_message=user_facing_error_message(exc),
            error_detail=failure_detail_from_exception(exc),
            failure_category=category,
            integration_involved=integration,
            integration_failure_message=integration_detail,
        )

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.apply_investigation_result(final_state, trigger=task_command)

    from surfaces.interactive_shell.ui.components.key_reader import restore_stdin_terminal
    from surfaces.interactive_shell.ui.feedback import prompt_investigation_feedback

    pt_app = getattr(session, "pt_style_app", None)
    pt_app_running = pt_app is not None and getattr(pt_app, "is_running", False)
    if not pt_app_running:
        restore_stdin_terminal()
        prompt_investigation_feedback(final_state)
    return InvestigationOutcome(
        status="completed",
        target=normalized_target,
        investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
        final_state=final_state,
    )


__all__ = ["run_foreground_investigation"]
