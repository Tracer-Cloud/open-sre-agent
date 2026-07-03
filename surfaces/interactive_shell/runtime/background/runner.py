"""Helpers for launching session-local background investigations."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from platform.analytics.cli import track_investigation
from platform.analytics.source import EntrypointSource, TriggerMode
from platform.common.errors import OpenSREError
from surfaces.interactive_shell.runtime import (
    BackgroundInvestigationRecord,
    Session,
    TaskKind,
)
from surfaces.interactive_shell.runtime.background.notifications import (
    deliver_background_notifications,
)
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception

BackgroundRunFn = Callable[..., dict[str, Any]]


def _safe_console_print(console: Console, message: str) -> None:
    isatty = getattr(console.file, "isatty", None)
    stdout_context = patch_stdout(raw=True) if callable(isatty) and isatty() else nullcontext()
    with stdout_context:
        console.print(message)


def drain_background_notices(session: Session, console: Console) -> None:
    """Print queued background investigation status lines on the main REPL thread."""
    for message in session.drain_background_notices():
        _safe_console_print(console, message)


def _build_record(
    *,
    task_id: str,
    command: str,
    investigation_id: str,
) -> BackgroundInvestigationRecord:
    return BackgroundInvestigationRecord(
        task_id=task_id,
        status="running",
        command=command,
        investigation_id=investigation_id,
    )


def _top_analysis(final_state: dict[str, Any]) -> tuple[str, ...]:
    claims = final_state.get("validated_claims", [])
    if not isinstance(claims, list):
        return ()
    lines: list[str] = []
    for entry in claims:
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim") or "").strip()
        if claim:
            lines.append(claim)
        if len(lines) >= 3:
            break
    return tuple(lines)


def _next_steps(final_state: dict[str, Any]) -> tuple[str, ...]:
    steps = final_state.get("remediation_steps", [])
    if not isinstance(steps, list):
        return ()
    values: list[str] = []
    for step in steps[:3]:
        text = str(step).strip()
        if text:
            values.append(text)
    return tuple(values)


def _stats(final_state: dict[str, Any]) -> dict[str, Any]:
    tool_calls = final_state.get("evidence_entries", [])
    loops = final_state.get("investigation_loop_count", 0)
    validity = final_state.get("validity_score", 0.0)
    return {
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "investigation_loop_count": int(loops) if isinstance(loops, int | float) else 0,
        "validity_score": float(validity) if isinstance(validity, int | float) else 0.0,
    }


def _start_background_investigation(
    *,
    session: Session,
    console: Console,
    display_command: str,
    run_fn: BackgroundRunFn,
    kwargs: dict[str, Any],
    investigation_target: str = "",
    input_path: str | None = None,
) -> str:
    investigation_id = str(uuid4())
    session.last_investigation_id = investigation_id
    task = session.task_registry.create(TaskKind.INVESTIGATION, command=display_command)
    task.mark_running()
    record = _build_record(
        task_id=task.task_id,
        command=display_command,
        investigation_id=investigation_id,
    )
    session.background_investigations[task.task_id] = record

    def _worker() -> None:
        try:
            with track_investigation(
                entrypoint=EntrypointSource.CLI_REPL_FILE,
                trigger_mode=TriggerMode.FILE,
                input_path=input_path,
                interactive=True,
                investigation_id=investigation_id,
                investigation_target=investigation_target or None,
                session=session,
            ):
                final_state = run_fn(cancel_requested=task.cancel_requested, **kwargs)
            root = str(final_state.get("root_cause") or "")
            record.status = "completed"
            record.root_cause = root
            record.top_analysis = _top_analysis(final_state)
            record.next_steps = _next_steps(final_state)
            record.stats = _stats(final_state)
            record.final_state = dict(final_state)
            record.notification_results = deliver_background_notifications(
                record=record,
                channels=session.background_notification_preferences.channels,
            )
            task.mark_completed(result=root)
            session.enqueue_background_notice(
                f"[{HIGHLIGHT}]background investigation complete[/] "
                f"[{DIM}]— task {escape(task.task_id)} ready; "
                f"use[/] [{HIGHLIGHT}]/background show {escape(task.task_id)}[/]",
            )
        except KeyboardInterrupt:
            record.status = "cancelled"
            task.mark_cancelled()
            session.enqueue_background_notice(
                f"[{WARNING}]background investigation cancelled[/] "
                f"[{DIM}]for task {escape(task.task_id)}.[/]",
            )
        except OpenSREError as exc:
            record.status = "failed"
            task.mark_failed(str(exc))
            session.enqueue_background_notice(
                f"[{ERROR}]background investigation failed[/] "
                f"[{DIM}]for task {escape(task.task_id)}:[/] {escape(str(exc))}",
            )
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            task.mark_failed(str(exc))
            report_exception(exc, context="surfaces.interactive_shell.background_investigation")
            session.enqueue_background_notice(
                f"[{ERROR}]background investigation failed[/] "
                f"[{DIM}]for task {escape(task.task_id)}:[/] {escape(str(exc))}",
            )

    thread = threading.Thread(
        target=_worker,
        daemon=True,
        name=f"background-investigation-{task.task_id}",
    )
    thread.start()
    _safe_console_print(
        console,
        f"[{DIM}]background investigation started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/background list[/] [{DIM}]to monitor, "
        f"[/][{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]",
    )
    return task.task_id


def start_background_text_investigation(
    *,
    alert_text: str,
    session: Session,
    console: Console,
    display_command: str = "background free-text investigation",
    investigation_target: str = "",
) -> str:
    from surfaces.cli.investigation import run_investigation_for_session_background

    return _start_background_investigation(
        session=session,
        console=console,
        display_command=display_command,
        run_fn=run_investigation_for_session_background,
        kwargs={
            "alert_text": alert_text,
            "context_overrides": session.accumulated_context or None,
        },
        investigation_target=investigation_target,
        input_path=display_command,
    )


def start_background_template_investigation(
    *,
    template_name: str,
    session: Session,
    console: Console,
    display_command: str,
    investigation_target: str = "",
) -> str:
    from surfaces.cli.investigation import run_sample_alert_for_session_background

    return _start_background_investigation(
        session=session,
        console=console,
        display_command=display_command,
        run_fn=run_sample_alert_for_session_background,
        kwargs={
            "template_name": template_name,
            "context_overrides": session.accumulated_context or None,
        },
        investigation_target=investigation_target,
        input_path=f"template:{template_name}",
    )
