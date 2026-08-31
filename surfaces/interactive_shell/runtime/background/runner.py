"""Helpers for launching session-local background investigations."""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, Protocol, TypedDict, cast
from uuid import uuid4

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from infrastructure.analytics.investigation_tracker import track_investigation
from infrastructure.analytics.source import EntrypointSource, TriggerMode
from infrastructure.errors import OpenSREError
from surfaces.interactive_shell.runtime import (
    BackgroundInvestigationRecord,
    Session,
    TaskKind,
)
from surfaces.interactive_shell.runtime.background.notifications import (
    deliver_background_notifications,
)
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.error_handling.exception_reporting import report_exception


class BackgroundRunResult(TypedDict, total=False):
    """Fields consumed from a background investigation result."""

    root_cause: str
    validated_claims: list[dict[str, Any]]
    remediation_steps: list[str]
    evidence_entries: list[Any]
    investigation_loop_count: int
    validity_score: float


class BackgroundRunFn(Protocol):
    """Callable contract for running a background investigation."""

    def __call__(
        self,
        *,
        cancel_requested: threading.Event,
    ) -> BackgroundRunResult:
        """Run a background investigation with cooperative cancellation support."""


def _persist_record(session: Session, record: BackgroundInvestigationRecord) -> None:
    """Save the record so it outlives this REPL session.

    Never raises. The call sits in a ``finally`` on a daemon thread, so an escaping
    exception could not fail the investigation (the ``except`` arms have already
    run) but would reach ``threading.excepthook`` and print a traceback straight
    into the terminal prompt_toolkit is drawing.

    Failures report through both channels the arms above use, because the CLI
    configures no logging and a lost record is otherwise invisible.
    """
    from infrastructure.scheduling.background_investigations.store import (
        UnreadableStoreError,
        open_record_store,
    )

    try:
        open_record_store().save(record)
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="surfaces.interactive_shell.background_persist")
        # A damaged document fails every later save too, so name it rather than
        # repeating an unactionable notice after every investigation. That message
        # already carries the path, and the local terminal is not an external sink.
        detail = (
            escape(str(exc))
            if isinstance(exc, UnreadableStoreError)
            else escape(type(exc).__name__)
        )
        session.terminal.enqueue_background_notice(
            f"[{WARNING}]background record not saved[/] "
            f"[{DIM}]for task {escape(record.task_id)}:[/] {detail}",
        )


def _safe_console_print(console: Console, message: str) -> None:
    isatty = getattr(console.file, "isatty", None)
    stdout_context = patch_stdout(raw=True) if callable(isatty) and isatty() else nullcontext()
    with stdout_context:
        console.print(message)


def drain_background_notices(session: Session, console: Console) -> None:
    """Print queued background investigation status lines on the main REPL thread."""
    for message in session.terminal.drain_background_notices():
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


def _top_analysis(final_state: Mapping[str, Any]) -> tuple[str, ...]:
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


def _next_steps(final_state: Mapping[str, Any]) -> tuple[str, ...]:
    steps = final_state.get("remediation_steps", [])
    if not isinstance(steps, list):
        return ()
    values: list[str] = []
    for step in steps[:3]:
        text = str(step).strip()
        if text:
            values.append(text)
    return tuple(values)


def _stats(final_state: Mapping[str, Any]) -> dict[str, Any]:
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
    session.terminal.background_investigations[task.task_id] = record

    def _worker() -> None:
        from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context

        with bound_usage_context(
            surface=UsageSurface.CLI,
            session_id=session.session_id,
        ):
            try:
                with track_investigation(
                    entrypoint=EntrypointSource.CLI_REPL_FILE,
                    trigger_mode=TriggerMode.FILE,
                    input_path=input_path,
                    interactive=True,
                    investigation_id=investigation_id,
                    investigation_target=investigation_target or None,
                    session=session,
                ) as tracker:
                    final_state = run_fn(cancel_requested=task.cancel_requested)
                    tracker.record_loop_metrics_from_state(final_state)
                root = str(final_state.get("root_cause") or "")
                record.status = "completed"
                record.root_cause = root
                record.top_analysis = _top_analysis(final_state)
                record.next_steps = _next_steps(final_state)
                record.stats = _stats(final_state)
                record.final_state = dict(final_state)
                record.notification_results = deliver_background_notifications(
                    record=record,
                    channels=session.terminal.background_notification_preferences.channels,
                )
                task.mark_completed(result=root)
                session.terminal.enqueue_background_notice(
                    f"[{HIGHLIGHT}]background investigation complete[/] "
                    f"[{DIM}]— task {escape(task.task_id)} ready; "
                    f"use[/] [{HIGHLIGHT}]/background show {escape(task.task_id)}[/]",
                )
            except KeyboardInterrupt:
                record.status = "cancelled"
                task.mark_cancelled()
                session.terminal.enqueue_background_notice(
                    f"[{WARNING}]background investigation cancelled[/] "
                    f"[{DIM}]for task {escape(task.task_id)}.[/]",
                )
            except OpenSREError as exc:
                record.status = "failed"
                task.mark_failed(str(exc))
                session.terminal.enqueue_background_notice(
                    f"[{ERROR}]background investigation failed[/] "
                    f"[{DIM}]for task {escape(task.task_id)}:[/] {escape(str(exc))}",
                )
            except Exception as exc:  # noqa: BLE001
                record.status = "failed"
                task.mark_failed(str(exc))
                report_exception(exc, context="surfaces.interactive_shell.background_investigation")
                session.terminal.enqueue_background_notice(
                    f"[{ERROR}]background investigation failed[/] "
                    f"[{DIM}]for task {escape(task.task_id)}:[/] {escape(str(exc))}",
                )
            finally:
                # After the arms above, so persistence can never alter task state.
                _persist_record(session, record)

    # Copy the context so the per-turn storage scope (a ContextVar set by
    # ``bound_storage_scope``) is inherited. Without it ``current_scope()`` is None
    # on the worker, and everything it touches that resolves through
    # ``opensre_home()`` or ``session_home()`` — session transcripts, memory,
    # integration reads — falls back to the shared host root instead of the bound
    # organization's. Same reason as ``memory_extraction._schedule_coalesced``.
    #
    # The record store no longer depends on this: ``deployment_home()`` falls back
    # to the configured organization rather than the host root, and a transport
    # always binds that same organization. Kept because the rest of the worker does.
    thread = threading.Thread(
        target=contextvars.copy_context().run,
        args=(_worker,),
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
    from surfaces.interactive_shell.runtime.investigation_adapter import (
        run_investigation_for_session_background,
    )

    context_overrides = dict(session.accumulated_context) or None

    def _run(*, cancel_requested: threading.Event) -> BackgroundRunResult:
        return cast(
            BackgroundRunResult,
            run_investigation_for_session_background(
                alert_text=alert_text,
                context_overrides=context_overrides,
                cancel_requested=cancel_requested,
            ),
        )

    return _start_background_investigation(
        session=session,
        console=console,
        display_command=display_command,
        run_fn=_run,
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
    from surfaces.interactive_shell.runtime.investigation_adapter import (
        run_sample_alert_for_session_background,
    )

    context_overrides = dict(session.accumulated_context) or None

    def _run(*, cancel_requested: threading.Event) -> BackgroundRunResult:
        return cast(
            BackgroundRunResult,
            run_sample_alert_for_session_background(
                template_name=template_name,
                context_overrides=context_overrides,
                cancel_requested=cancel_requested,
            ),
        )

    return _start_background_investigation(
        session=session,
        console=console,
        display_command=display_command,
        run_fn=_run,
        investigation_target=investigation_target,
        input_path=f"template:{template_name}",
    )
