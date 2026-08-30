"""Shared foreground investigation task lifecycle for REPL entry points."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from core.agent_harness.spi.session_state import session_terminal
from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from infrastructure.errors import OpenSREError
from infrastructure.observability.trace.spans import mark_span_outcome, traced_session
from infrastructure.scheduling.task_types import TaskKind, TaskRecord
from infrastructure.terminal.theme import DIM, ERROR, WARNING
from surfaces.interactive_shell.telemetry.investigation_llm_usage import (
    InvestigationLlmUsage,
    observe_investigation_llm_usage,
    resolve_configured_llm_identity,
)
from surfaces.interactive_shell.ui.investigation_outcome import (
    ForegroundInvestigationStatus,
    InvestigationOutcome,
    classify_investigation_failure,
    failure_detail_from_exception,
    normalize_investigation_target,
    user_facing_error_message,
)
from surfaces.shared.error_handling.exception_reporting import report_exception

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session


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
    session_id = str(getattr(session, "session_id", "") or "") or None
    # The action agent cannot call update_plan mid-investigation, so the host
    # advances the pinned overlay: ProgressTracker / diagnose read the registered
    # session, and a completed run marks every step done.
    from surfaces.shared.terminal.output.console_state import (
        complete_investigation_plan,
        set_investigation_plan_session,
    )

    set_investigation_plan_session(session)
    try:
        with traced_session(
            session_id,
            component="investigation",
            attributes={"target": normalized_target, "command": task_command},
        ) as attrs:
            outcome = _run_foreground_investigation_body(
                session=session,
                console=console,
                task_command=task_command,
                run=run,
                exception_context=exception_context,
                normalized_target=normalized_target,
                task=task,
                started=started,
            )
            if outcome.status is ForegroundInvestigationStatus.COMPLETED:
                complete_investigation_plan()
                from core.agent_harness.task_plan.work_log import take_completed_plan_breakdown

                breakdown = take_completed_plan_breakdown(session)
                if breakdown:
                    console.print()
                    console.print(breakdown, markup=False)
            mark_span_outcome(
                attrs,
                outcome.status,
                error=bool(outcome.failure_category),
                investigation_id=outcome.investigation_id or None,
                failure_category=outcome.failure_category or None,
            )
            return outcome
    finally:
        set_investigation_plan_session(None)


def _run_foreground_investigation_body(
    *,
    session: Session,
    console: Console,
    task_command: str,
    run: Callable[[TaskRecord], dict[str, Any]],
    exception_context: str,
    normalized_target: str,
    task: TaskRecord,
    started: float,
) -> InvestigationOutcome:
    try:
        with observe_investigation_llm_usage() as usage:
            final_state = run(task)
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        return InvestigationOutcome(
            status=ForegroundInvestigationStatus.CANCELLED,
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
            status=ForegroundInvestigationStatus.FAILED,
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
            status=ForegroundInvestigationStatus.FAILED,
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

    from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
    from surfaces.shared.terminal.components.key_reader import restore_stdin_terminal
    from surfaces.shared.terminal.feedback import prompt_investigation_feedback

    # Skip feedback while the prompt-toolkit app is running: its cursor-position
    # queries would race the raw feedback menu and leak bytes into the next prompt.
    terminal = session_terminal(session)
    prompt_app_running = False
    if terminal is not None:
        prompt_app = terminal.prompt_app
        prompt_app_running = prompt_app is not None and getattr(prompt_app, "is_running", False)
    # RCA feedback is REPL-only: gateway/headless sessions have no terminal facet and
    # must not block on a raw-stdin picker (e.g. gateway running under tmux with TTY).
    if terminal is not None and not prompt_app_running and repl_tty_interactive():
        restore_stdin_terminal()
        prompt_investigation_feedback(final_state)
    return InvestigationOutcome(
        status=ForegroundInvestigationStatus.COMPLETED,
        target=normalized_target,
        investigation_id=str(getattr(session, "last_investigation_id", "") or ""),
        final_state=final_state,
        **_llm_fields(usage, started),
    )


__all__ = ["run_foreground_investigation"]
