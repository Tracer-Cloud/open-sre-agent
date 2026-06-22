from __future__ import annotations

import os
import textwrap
import time
from collections.abc import Callable
from typing import Any

from app.cli.interactive_shell.ui.output.console_state import _get_console
from app.cli.interactive_shell.ui.output.environment import (
    _is_silent_output,
    _repl_progress_active,
    _safe_print,
    get_output_format,
)
from app.cli.interactive_shell.ui.output.events import ProgressEvent
from app.cli.interactive_shell.ui.output.labels import _humanise_message, _node_label
from app.cli.interactive_shell.ui.time_format import _fmt_timing
from app.cli.interactive_shell.ui.output.live_display import _EventLogDisplay
from app.cli.interactive_shell.ui.output.repl_display import _ReplEventLogDisplay
from app.cli.interactive_shell.ui.output.toggles import (
    CtrlOToggleWatcher,
    register_tool_detail_toggle,
)
from app.cli.interactive_shell.ui.output.tool_tracking import ToolTrackingMixin

Display = _EventLogDisplay | _ReplEventLogDisplay


def _make_event_log_display(*, t0: float) -> Display:
    return _ReplEventLogDisplay(t0=t0) if _repl_progress_active() else _EventLogDisplay(t0=t0)


class ProgressTracker(ToolTrackingMixin):
    """Drives event-log displays from node lifecycle calls."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._start_times: dict[str, float] = {}
        self._t0 = time.monotonic()
        self._silent = _is_silent_output()
        self._rich = get_output_format() == "rich"
        self._repl_append_only = _repl_progress_active()
        self._display: Display | None = None
        self._tool_start_times: dict[str, float] = {}
        self._tool_inputs: dict[str, Any] = {}
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._printed_tool_detail_ids: set[int] = set()
        self._tool_summary_counts: dict[str, dict[str, int]] = {}
        self._tool_summary_order: list[tuple[str, str]] = []
        self._toggle_watcher: CtrlOToggleWatcher | None = None
        self._toggle_unregister: Callable[[], None] | None = None
        if self._rich and not self._silent:
            self._display = _make_event_log_display(t0=self._t0)
            self._toggle_unregister = register_tool_detail_toggle(self.toggle_tool_details)
            if not self._repl_append_only:
                self._toggle_watcher = CtrlOToggleWatcher(self.toggle_tool_details)
                self._toggle_watcher.start()

    @property
    def has_active_display(self) -> bool:
        return self._display is not None

    def stop(self) -> None:
        self._stop_toggle_watcher()
        if self._display:
            self._display.stop()
            self._display = None

    def _stop_toggle_watcher(self) -> None:
        if self._toggle_watcher is not None:
            self._toggle_watcher.stop()
            self._toggle_watcher = None
        if self._toggle_unregister is not None:
            self._toggle_unregister()
            self._toggle_unregister = None

    def start(self, node_name: str, message: str | None = None) -> None:
        self._start_times[node_name] = time.monotonic()
        self.events.append(
            ProgressEvent(node_name=node_name, elapsed_ms=0, status="started", message=message)
        )
        if self._silent:
            return
        if not self._rich:
            _safe_print(f"  … {_node_label(node_name)}")
            return
        if node_name == "publish_findings":
            self._stop_toggle_watcher()
            if self._display:
                self._display.stop()
                self._display = None
            return
        if self._display is None:
            self._display = _make_event_log_display(t0=self._t0)
        self._display.step_start(node_name)

    def complete(
        self,
        node_name: str,
        fields_updated: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        self._finish(node_name, "completed", fields_updated or [], message)

    def error(self, node_name: str, message: str) -> None:
        self._finish(node_name, "error", [], message)

    def update_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        if self._display:
            self._display.step_subtext(node_name, text, duration)

    def print_above(self, text: str) -> None:
        if self._silent:
            return
        if self._display:
            self._display.print_above(text)
            return
        if text.strip():
            cols = max(40, int(os.getenv("COLUMNS", "80")))
            for para in text.strip().splitlines():
                if not para.strip():
                    print()
                    continue
                for chunk in textwrap.wrap(para, width=max(40, cols - 2)) or [para]:
                    print(f"  {chunk}")

    def print_above_renderable(self, renderable: Any) -> None:
        if self._display:
            self._display.print_above_renderable(renderable)
        else:
            _get_console().print(renderable)

    def _finish(
        self,
        node_name: str,
        status: str,
        fields_updated: list[str],
        message: str | None,
    ) -> None:
        elapsed_ms = int(
            (time.monotonic() - self._start_times.pop(node_name, time.monotonic())) * 1000
        )
        event = ProgressEvent(node_name, elapsed_ms, fields_updated, status, message)
        self.events.append(event)
        if self._silent:
            return
        if self._rich:
            if self._display:
                self._display.step_complete(node_name, event)
            else:
                mark = "✗" if status == "error" else "●"
                line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
                if msg := _humanise_message(message or ""):
                    line += f"  {msg}"
                self.print_above_renderable(line)
            return
        mark = "✗" if status == "error" else "●"
        line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
        if msg := _humanise_message(message or ""):
            line += f"  {msg}"
        _safe_print(line)


_tracker: ProgressTracker | None = None


def get_tracker(*, reset: bool = False) -> ProgressTracker:
    global _tracker
    if _tracker is None or reset:
        if reset and _tracker is not None:
            _tracker.stop()
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    return get_tracker(reset=True)


def set_silent_tracker() -> None:
    global _tracker
    if _tracker is not None:
        _tracker.stop()
    _tracker = ProgressTracker.__new__(ProgressTracker)
    _tracker.events = []
    _tracker._start_times = {}
    _tracker._t0 = time.monotonic()
    _tracker._silent = True
    _tracker._rich = False
    _tracker._display = None
    _tracker._tool_start_times = {}
    _tracker._tool_inputs = {}
    _tracker._tool_details_visible = False
    _tracker._tool_detail_records = []
    _tracker._printed_tool_detail_ids = set()
    _tracker._tool_summary_counts = {}
    _tracker._tool_summary_order = []
    _tracker._toggle_watcher = None
    _tracker._toggle_unregister = None


def _stop_active_tracker_toggle_watcher() -> None:
    if _tracker is not None:
        _tracker._stop_toggle_watcher()
