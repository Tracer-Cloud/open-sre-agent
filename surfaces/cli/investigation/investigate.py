"""Shared investigation helpers for CLI entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, NoReturn

from core.domain.stream import StreamEvent
from platform.observability.trace.hook import traceable
from surfaces.cli.error_mapping import reraise_cli_runtime_error
from tools.investigation.session_runner import InvestigationPumpCancelled, check_llm_settings

_logger = logging.getLogger(__name__)

_SESSION_EVENT_POLL_S = 0.25

if TYPE_CHECKING:
    from platform.analytics.cli import InvestigationTracker


def _reraise_cli_investigation_failure(exc: BaseException) -> NoReturn:
    """Map investigation runtime failures to structured CLI errors."""
    from tools.investigation.session_runner import reraise_investigation_failure

    if isinstance(exc, InvestigationPumpCancelled):
        reraise_investigation_failure(exc)
    reraise_cli_runtime_error(exc)


@traceable(name="investigation")
def run_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
) -> dict[str, Any]:
    """Run the investigation and return the CLI-facing JSON payload.

    Thin CLI wrapper over :func:`tools.investigation.capability.run_investigation_payload`:
    it adds the CLI-only precondition check (LLM settings) and maps runtime failures to
    structured ``OpenSREError`` messages. The run itself and the result shaping live in
    ``core`` so non-CLI surfaces can reuse them without importing ``cli``.

    ``investigation_metadata`` is an optional ``(alert_name, pipeline_name, severity)``
    tuple for initial state (e.g. HTTP request overrides) without mutating ``raw_alert``.
    """
    check_llm_settings()
    from tools.investigation.capability import run_investigation_payload

    try:
        return run_investigation_payload(
            raw_alert=raw_alert,
            opensre_evaluate=opensre_evaluate,
            investigation_metadata=investigation_metadata,
        )
    except Exception as exc:
        _reraise_cli_investigation_failure(exc)


def stream_investigation_cli(
    *,
    raw_alert: dict[str, Any],
) -> Generator[StreamEvent]:
    """Stream investigation events locally via the async pipeline stream.

    Bridges the async streaming API into a synchronous iterator
    using a background thread + queue so events are yielded in real time
    (not batched).  The same ``StreamRenderer`` used for remote
    investigations can render local runs identically.

    On :exc:`KeyboardInterrupt` the background asyncio task is cancelled
    and the thread is joined so Ctrl+C terminates cleanly instead of
    leaving an orphaned investigation task in flight.
    """
    import queue

    from tools.investigation.capability import astream_investigation

    check_llm_settings()

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    pump_task_ref: dict[str, asyncio.Task[None]] = {}

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        loop_ref["loop"] = loop
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    raw_alert=raw_alert,
                ):
                    event_queue.put(evt)

            task = loop.create_task(_pump())
            pump_task_ref["task"] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                event_queue.put(InvestigationPumpCancelled())
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
            loop.call_soon_threadsafe(task.cancel)

    try:
        while True:
            try:
                item = event_queue.get(timeout=_SESSION_EVENT_POLL_S)
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                from platform.analytics.investigation_loop import (
                    publish_loop_metrics_from_stream_failure,
                )

                thread.join(timeout=5)
                _reraise_cli_investigation_failure(publish_loop_metrics_from_stream_failure(item))
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
    tracker: InvestigationTracker | None = None,
) -> dict[str, Any]:
    """Run the investigation with real-time streaming UI and return the result.

    Uses async pipeline streaming + ``StreamRenderer`` so the local CLI shows
    the same live tool-call and reasoning updates as a remote investigation.
    """
    from surfaces.cli.ui.renderer import StreamRenderer

    events = stream_investigation_cli(
        raw_alert=raw_alert,
    )
    renderer = StreamRenderer(local=True)
    try:
        final_state = renderer.render_stream(events)
    except KeyboardInterrupt:
        events.close()
        raise

    from surfaces.interactive_shell.ui.components.key_reader import restore_stdin_terminal
    from surfaces.interactive_shell.ui.feedback import prompt_investigation_feedback

    restore_stdin_terminal()
    prompt_investigation_feedback(final_state)
    if tracker is not None:
        tracker.record_loop_metrics_from_state(final_state)
    return {
        "report": final_state.get("slack_message", final_state.get("report", "")),
        "problem_md": final_state.get("problem_md", ""),
        "root_cause": final_state.get("root_cause", ""),
        "is_noise": final_state.get("is_noise", False),
        "tool_calls": final_state.get("evidence_entries", []),
    }
