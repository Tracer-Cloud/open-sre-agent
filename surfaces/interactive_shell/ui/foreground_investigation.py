"""Shared foreground investigation task lifecycle for REPL entry points."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from platform.common.errors import OpenSREError
from platform.common.task_types import TaskKind, TaskRecord
from platform.terminal.theme import DIM, ERROR, WARNING
from surfaces.interactive_shell.ui.investigation_outcome import (
    InvestigationOutcome,
    classify_investigation_failure,
    failure_detail_from_exception,
    normalize_investigation_target,
    user_facing_error_message,
)
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception
from surfaces.interactive_shell.utils.telemetry.investigation_llm_usage import (
    InvestigationLlmUsage,
    observe_investigation_llm_usage,
    resolve_configured_llm_identity,
)

if TYPE_CHECKING:
    from core.agent_harness.session import Session


def _render_credit_exhausted_recovery_hint(console: Console, message: str) -> None:
    if CREDIT_EXHAUSTED_MARKER not in message:
        return
    console.print(f"[{DIM}]Run /model to switch to another provider.[/]")
    console.print(
        f"[{DIM}]Or run /auth login <provider> to re-authenticate or add a different provider.[/]"
    )


def _contains_auth_login_hint(message: str | None) -> bool:
    if not message:
        return False
    return "auth login" in message


def _llm_fields(usage: InvestigationLlmUsage, started: float) -> dict[str, Any]:
    """LLM identity, token, and timing fields shared by every outcome shape."""
    provider, configured_model = resolve_configured_llm_identity()
    return {
        "llm_model": usage.model or configured_model,
        "llm_provider": provider,
        "llm_input_tokens": usage.input_tokens,
        "llm_output_tokens": usage.output_tokens,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


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
    started = time.monotonic()
    try:
        with observe_investigation_llm_usage() as usage:
            final_state = run(task)
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        return InvestigationOutcome(
            status="cancelled",
            target=normalized_target,
            investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
            failure_category="user_cancelled",
            **_llm_fields(usage, started),
        )
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        message = str(exc)
        console.print(f"[{ERROR}]investigation failed:[/] {escape(message)}")
        if not _contains_auth_login_hint(exc.suggestion):
            _render_credit_exhausted_recovery_hint(console, message)
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
            **_llm_fields(usage, started),
        )
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context=exception_context)
        message = str(exc)
        console.print(f"[{ERROR}]investigation failed:[/] {escape(message)}")
        _render_credit_exhausted_recovery_hint(console, message)
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
            **_llm_fields(usage, started),
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
        **_llm_fields(usage, started),
    )


__all__ = ["run_foreground_investigation"]
