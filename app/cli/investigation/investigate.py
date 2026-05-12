"""Shared investigation helpers for CLI entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Generator, Iterator
from typing import TYPE_CHECKING, Any, NoReturn

from langsmith import traceable

from app.cli.support.cli_error_mapping import reraise_cli_runtime_error
from app.config import LLMSettings

if TYPE_CHECKING:
    from app.remote.stream import StreamEvent
    from app.state import AgentState

_logger = logging.getLogger(__name__)


class _InvestigationPumpCancelled(Exception):
    """Propagated when the async pump task was cancelled (distinct from Ctrl+C SIGINT)."""


_SESSION_EVENT_POLL_S = 0.25


def _check_llm_settings() -> None:
    """Validate LLM settings early and surface misconfiguration as a structured error."""
    from pydantic import ValidationError

    from app.cli.support.errors import OpenSREError

    try:
        LLMSettings.from_env()
    except ValidationError as exc:
        errors = exc.errors()
        if errors:
            ctx = errors[0].get("ctx", {})
            original = ctx.get("error")
            msg = str(original) if isinstance(original, Exception) else errors[0]["msg"]
        else:
            msg = str(exc)
        raise OpenSREError(
            msg,
            suggestion="Run `opensre onboard` to configure your LLM provider and API credentials.",
        ) from exc


def _reraise_investigation_failure(exc: BaseException) -> NoReturn:
    """Map investigation runtime failures to structured CLI errors."""
    if isinstance(exc, _InvestigationPumpCancelled):
        from app.cli.support.errors import OpenSREError

        raise OpenSREError(
            "Investigation streaming stopped before completion.",
            suggestion="The run was cancelled or closed early. Retry if you still need results.",
        ) from exc

    reraise_cli_runtime_error(exc)


def _call_run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    *,
    raw_alert: dict[str, Any],
    openclaw_context: dict[str, Any] | None = None,
    opensre_evaluate: bool = False,
) -> AgentState:
    """Import the heavy investigation runner only when execution starts."""
    from app.pipeline.runners import run_investigation

    return run_investigation(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
        openclaw_context=openclaw_context,
        opensre_evaluate=opensre_evaluate,
    )


def resolve_investigation_context(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    pipeline_name: str | None,
    severity: str | None,
) -> tuple[str, str, str]:
    """Resolve investigation metadata from CLI overrides and payload defaults."""
    return (
        alert_name or raw_alert.get("alert_name") or "Incident",
        pipeline_name or raw_alert.get("pipeline_name") or "events_fact",
        severity or raw_alert.get("severity") or "warning",
    )


@traceable(name="investigation")
def run_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
    openclaw_context: dict[str, Any] | None = None,
    opensre_evaluate: bool = False,
) -> dict[str, Any]:
    """Run the investigation and return the CLI-facing JSON payload."""
    _check_llm_settings()
    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    try:
        state = _call_run_investigation(
            resolved_alert_name,
            resolved_pipeline_name,
            resolved_severity,
            raw_alert=raw_alert,
            openclaw_context=openclaw_context,
            opensre_evaluate=opensre_evaluate,
        )
    except Exception as exc:
        _reraise_investigation_failure(exc)
    slack_message = state["slack_message"]
    out: dict[str, Any] = {
        "report": slack_message,
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
        "is_noise": state.get("is_noise", False),
    }
    if opensre_evaluate:
        ev = state.get("opensre_llm_eval")
        if isinstance(ev, dict) and ev:
            out["opensre_llm_eval"] = ev
        elif not (state.get("opensre_eval_rubric") or "").strip():
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": (
                    "No scoring_points on this alert — nothing to judge against "
                    "(not an OpenRCA rubric payload, or field missing)."
                ),
            }
        else:
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": "Evaluate was enabled but no judge output was recorded.",
            }
    return out


def stream_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> Generator[StreamEvent]:
    """Stream investigation events locally via ``astream_events``.

    Bridges the async LangGraph streaming API into a synchronous iterator
    using a background thread + queue so events are yielded in real time
    (not batched).  The same ``StreamRenderer`` used for remote
    investigations can render local runs identically.

    On :exc:`KeyboardInterrupt` the background asyncio task is cancelled
    and the thread is joined so Ctrl+C terminates cleanly instead of
    leaving an orphaned LangGraph run in flight.
    """
    import queue
    import threading

    from app.pipeline.runners import astream_investigation

    _check_llm_settings()
    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    pump_task_ref: dict[str, asyncio.Task[None]] = {}

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        loop_ref["loop"] = loop
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    resolved_alert_name,
                    resolved_pipeline_name,
                    resolved_severity,
                    raw_alert=raw_alert,
                ):
                    event_queue.put(evt)

            task = loop.create_task(_pump())
            pump_task_ref["task"] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                event_queue.put(_InvestigationPumpCancelled())
        except Exception as exc:
            event_queue.put(exc)
        finally:
            event_queue.put(None)
            loop.close()

    thread = threading.Thread(target=_run_async, daemon=True)
    thread.start()

    def _cancel_pump() -> None:
        loop = loop_ref.get("loop")
        task = pump_task_ref.get("task")
        if loop is None or task is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            # Loop may close between `is_closed()` and scheduling cancellation.
            loop.call_soon_threadsafe(task.cancel)

    try:
        while True:
            try:
                item = event_queue.get(timeout=_SESSION_EVENT_POLL_S)
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                thread.join(timeout=5)
                _reraise_investigation_failure(item)
            if item is None:
                break
            yield item
    finally:
        _cancel_pump()
        thread.join(timeout=5)
        if thread.is_alive():
            _logger.warning(
                "investigation thread did not terminate within 5s after cancellation; "
                "an LLM call may still be in flight"
            )


def run_investigation_cli_streaming(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Run the investigation with real-time streaming UI and return the result.

    Uses ``astream_events`` + ``StreamRenderer`` so the local CLI shows
    the same live tool-call and reasoning updates as a remote investigation.
    """
    from app.remote.renderer import StreamRenderer

    events = stream_investigation_cli(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    renderer = StreamRenderer(local=True)
    try:
        final_state = renderer.render_stream(events)
    except KeyboardInterrupt:
        # Force-close the generator so the background thread's finally block
        # runs and the async task is cancelled before we re-raise.
        events.close()
        raise
    return {
        "report": final_state.get("slack_message", final_state.get("report", "")),
        "problem_md": final_state.get("problem_md", ""),
        "root_cause": final_state.get("root_cause", ""),
        "is_noise": final_state.get("is_noise", False),
    }


def _run_session_alert_payload(
    *,
    raw_alert: dict[str, Any],
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a streaming investigation from an already-structured session alert."""
    import queue

    from app.pipeline.runners import astream_investigation
    from app.remote.renderer import StreamRenderer

    _check_llm_settings()
    if context_overrides:
        raw_alert.setdefault("annotations", {}).update(context_overrides)

    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=None,
        pipeline_name=None,
        severity=None,
    )

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    pump_task_ref: dict[str, asyncio.Task[None]] = {}

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        loop_ref["loop"] = loop
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    resolved_alert_name,
                    resolved_pipeline_name,
                    resolved_severity,
                    raw_alert=raw_alert,
                ):
                    event_queue.put(evt)

            task = loop.create_task(_pump())
            pump_task_ref["task"] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                event_queue.put(_InvestigationPumpCancelled())
        except Exception as exc:
            event_queue.put(exc)
        finally:
            event_queue.put(None)
            loop.close()

    thread = threading.Thread(target=_run_async, daemon=True)
    thread.start()

    def _cancel_pump() -> None:
        loop = loop_ref.get("loop")
        task = pump_task_ref.get("task")
        if loop is None or task is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            # Loop may close between `is_closed()` and scheduling cancellation.
            loop.call_soon_threadsafe(task.cancel)

    def _events() -> Iterator[StreamEvent]:
        try:
            while True:
                if cancel_requested is not None and cancel_requested.is_set():
                    _cancel_pump()
                    raise KeyboardInterrupt
                try:
                    item = event_queue.get(timeout=_SESSION_EVENT_POLL_S)
                except queue.Empty:
                    continue
                if isinstance(item, BaseException):
                    thread.join(timeout=5)
                    _reraise_investigation_failure(item)
                if item is None:
                    return
                yield item
        finally:
            _cancel_pump()

    renderer = StreamRenderer(local=True)
    try:
        final_state = renderer.render_stream(_events())
    except KeyboardInterrupt:
        _cancel_pump()
        raise
    finally:
        # Always join so unexpected exceptions from render_stream don't leak
        # the daemon thread and leave an orphaned LLM call running.
        thread.join(timeout=5)
        if thread.is_alive():
            _logger.warning(
                "investigation thread did not terminate within 5s after cancellation; "
                "an LLM call may still be in flight"
            )
    return dict(final_state)


def run_investigation_for_session(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a streaming investigation from a free-text alert description.

    Used by the REPL loop: wraps the user's text as the alert payload, runs
    the full pipeline with live streaming, and returns the final state so
    follow-ups and context accumulation can reference it.

    KeyboardInterrupt in the main thread is forwarded to the background
    asyncio loop as a task cancel, so Ctrl+C unwinds the in-flight LangGraph
    run cleanly instead of leaving it orphaned.

    When ``cancel_requested`` is set, the streaming loop polls it and cancels
    the pump the same way (used by the interactive shell task table).

    While this function runs, the synchronous REPL cannot process ``/cancel`` —
    Ctrl+C remains the interactive cancel path; the event wiring exists for a
    future non-blocking investigation driver or tooling that sets the flag.
    """
    raw_alert: dict[str, Any] = {"alert_name": "Interactive session", "message": alert_text}
    return _run_session_alert_payload(
        raw_alert=raw_alert,
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
    )


def run_sample_alert_for_session(
    *,
    template_name: str = "generic",
    context_overrides: dict[str, Any] | None = None,
    cancel_requested: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a streaming investigation for a built-in sample alert."""
    from app.cli.investigation.alert_templates import build_alert_template

    return _run_session_alert_payload(
        raw_alert=build_alert_template(template_name),
        context_overrides=context_overrides,
        cancel_requested=cancel_requested,
    )
