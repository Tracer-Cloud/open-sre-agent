"""
Output utilities shared across nodes.

- Typed event-log renderer: render_event(), render_footer(), render_divider()
- ProgressTracker: thin wrapper that drives the event log from node lifecycle calls
- Investigation header display
- Debug output (verbose mode)
- Environment detection (rich TTY vs plain text)
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from app.tools.registry import resolve_tool_display_name

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Environment detection
# ─────────────────────────────────────────────────────────────────────────────


def get_output_format() -> str:
    """Return 'rich' for interactive TTY, 'text' otherwise.

    Respects the ``NO_COLOR`` environment variable (https://no-color.org/).
    """
    if fmt := os.getenv("TRACER_OUTPUT_FORMAT"):
        return fmt
    if os.getenv("NO_COLOR") is not None:
        return "text"
    if os.getenv("SLACK_WEBHOOK_URL"):
        return "text"
    return "rich" if sys.stdout.isatty() else "text"


# ─────────────────────────────────────────────────────────────────────────────
# Design tokens (mirrors theme.py but scoped to output — no CLI dependency)
# ─────────────────────────────────────────────────────────────────────────────

_C_PRIMARY = "#1AFF8C"
_C_ACCENT = "#00D4C8"
_C_WARNING = "#F0A500"
_C_ERROR = "#FF4D6A"
_C_TEXT = "#E8EFE8"
_C_TEXT_DIM = "#6B8C6B"
_C_BORDER = "#2D4A2D"
_C_ORANGE = "#FF8C42"
_C_TEAL_SOFT = "#5EF0E8"


# ─────────────────────────────────────────────────────────────────────────────
# Badge registry
# ─────────────────────────────────────────────────────────────────────────────

# (padded_label, text_color)  — all labels are 6 chars wide
_BADGE_STYLES: dict[str, tuple[str, str]] = {
    "READ": ("READ  ", _C_PRIMARY),
    "PLAN": ("PLAN  ", _C_TEAL_SOFT),
    "INVEST": ("INVEST", _C_WARNING),
    "DIAG": ("DIAG  ", _C_ORANGE),
    "MERGE": ("MERGE ", _C_TEXT_DIM),
}

_NODE_EVENT_TYPE: dict[str, str] = {
    "extract_alert": "READ",
    "resolve_integrations": "READ",
    "plan_actions": "PLAN",
    "merge_hypotheses": "MERGE",
    "diagnose_root_cause": "DIAG",
    "opensre_llm_eval": "DIAG",
    "publish_findings": "DIAG",
}

_NODE_PHASE: dict[str, str] = {
    "extract_alert": "LOAD",
    "resolve_integrations": "LOAD",
    "plan_actions": "PLAN",
    "merge_hypotheses": "DIAGNOSE",
    "diagnose_root_cause": "DIAGNOSE",
    "opensre_llm_eval": "DIAGNOSE",
    "publish_findings": "PUBLISH",
}


def _node_event_type(node_name: str) -> str:
    if node_name.startswith("investigate"):
        return "INVEST"
    return _NODE_EVENT_TYPE.get(node_name, "DIAG")


def _node_phase_label(node_name: str) -> str:
    if node_name.startswith("investigate"):
        return "INVESTIGATE"
    return _NODE_PHASE.get(node_name, node_name.upper()[:12])


# ─────────────────────────────────────────────────────────────────────────────
# Node labels and helpers
# ─────────────────────────────────────────────────────────────────────────────

_NODE_LABELS: dict[str, str] = {
    "extract_alert": "Reading alert",
    "resolve_integrations": "Loading integrations",
    "plan_actions": "Planning",
    "investigate": "Gathering evidence",
    "diagnose_root_cause": "Diagnosing",
    "publish_findings": "Publishing",
}


def _node_label(node_name: str) -> str:
    if node_name.startswith("investigate_"):
        action = node_name[len("investigate_") :]
        return f"Investigate  · {action.replace('_', ' ').title()}"
    return _NODE_LABELS.get(node_name, node_name.replace("_", " ").title())


def _humanise_message(message: str) -> str:
    if not message:
        return ""
    m = re.match(r"Planned actions:\s*\[(.+)\]", message)
    if m:
        raw = re.findall(r"'([^']+)'", m.group(1))
        return ", ".join(resolve_tool_display_name(action) for action in raw)
    if "No new actions" in message:
        return ""
    if "integrations" in message.lower() or "resolved" in message.lower():
        m2 = re.search(r"\[(.+)\]", message)
        if m2 and (services := re.findall(r"'([^']+)'", m2.group(1))):
            return ", ".join(services)
    m3 = re.match(r"validity:(\d+%)", message)
    if m3:
        return f"confidence {m3.group(1)}"
    return re.sub(r"^datadog:", "", message)


def _fmt_timing(elapsed_ms: int) -> str:
    return f"{elapsed_ms / 1000:.1f}s" if elapsed_ms >= 1000 else f"{elapsed_ms}ms"


def _elapsed_hms(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# Module-level active console (set to Live's console while display is running)
# ─────────────────────────────────────────────────────────────────────────────

_live_console: Console | None = None
_active_display: _EventLogDisplay | None = None  # forward-declared below


def _get_console() -> Console:
    """Return the active Live console when the display is running, else a fresh one."""
    return _live_console or Console(highlight=False)


def stop_display() -> None:
    """Stop any running live display. Call before printing final report output."""
    global _active_display
    if _active_display is not None:
        _active_display.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Public rendering functions (spec: render_event, render_footer, render_divider)
# ─────────────────────────────────────────────────────────────────────────────


def render_divider(width: int = 80) -> None:
    """Print a BORDER-coloured dashed ┄ divider."""
    if get_output_format() == "rich":
        _get_console().print(Text("┄" * width, style=_C_BORDER))
    else:
        print("─" * width)


def render_footer(phase: str, elapsed: float, model: str, mode: str) -> None:
    """Print the persistent status footer line."""
    if get_output_format() == "rich":
        t = Text()
        t.append(" ● ", style=f"bold {_C_PRIMARY}")
        t.append(f"{phase}  ", style=f"bold {_C_TEXT_DIM}")
        t.append(f"{_elapsed_hms(elapsed)}  ", style=_C_TEXT_DIM)
        if model:
            t.append(f"{model}  ", style=_C_TEXT_DIM)
        t.append(f"{mode}  ", style=_C_TEXT_DIM)
        t.append("esc to cancel", style=f"dim {_C_TEXT_DIM}")
        _get_console().print(t)
    else:
        print(f"● {phase}  {elapsed:.1f}s  {model}  {mode}")


def render_event(
    event_type: str,
    message: str,
    *,
    insight: str | None = None,
    muted: bool = False,
    elapsed_s: float = 0.0,
    glyph: str = "✓",
    error: bool = False,
) -> None:
    """Print one typed event-log row."""
    if get_output_format() == "rich":
        badge_label, badge_color = _BADGE_STYLES.get(event_type, ("DIAG  ", _C_ORANGE))
        ts = _elapsed_hms(elapsed_s)
        t = Text()
        t.append(f"{ts}  ", style=_C_TEXT_DIM)
        if muted:
            t.append(f"{glyph}  ", style=_C_TEXT_DIM)
            msg_style = _C_TEXT_DIM
        elif error:
            t.append("✗  ", style=f"bold {_C_ERROR}")
            msg_style = _C_TEXT
        else:
            t.append(f"{glyph}  ", style=f"bold {_C_PRIMARY}")
            msg_style = _C_TEXT
        t.append(f"[{badge_label}]", style=f"bold {badge_color}")
        t.append("  ")
        t.append(message, style=msg_style)
        if insight:
            t.append(f"  ↳ {insight}", style=_C_ACCENT)
        _get_console().print(t)
    else:
        mark = "✗" if error else ("·" if muted else "✓")
        line = f"  {mark}  [{event_type}]  {message}"
        if insight:
            line += f"  ↳ {insight}"
        print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Live event-log display
# ─────────────────────────────────────────────────────────────────────────────

_SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
_FRAME_SECS = 0.10


class _LiveRenderable:
    """Rich renderable that rebuilds the event-log on every Live refresh."""

    def __init__(self, display: _EventLogDisplay) -> None:
        self._d = display

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        d = self._d
        now = time.monotonic()
        with d._lock:
            # Completed event lines (static)
            yield from d._completed

            # Active step lines (animated)
            for node_name, info in d._active_steps.items():
                elapsed_step = now - info["t0"]
                elapsed_total = now - d._t0
                frame = _SPINNER_FRAMES[int(elapsed_step / _FRAME_SECS) % len(_SPINNER_FRAMES)]
                ev_type = _node_event_type(node_name)
                badge_label, badge_color = _BADGE_STYLES.get(ev_type, ("DIAG  ", _C_ORANGE))
                label = _node_label(node_name)

                # subtext (tool calls / reasoning snippets)
                subtext: str | None = info.get("subtext")
                if subtext and now > info.get("subtext_until", 0.0):
                    subtext = None

                t = Text()
                t.append(f"{_elapsed_hms(elapsed_total)}  ", style=_C_TEXT_DIM)
                t.append(f"{frame}  ", style=_C_TEXT_DIM)
                t.append(f"[{badge_label}]", style=f"bold {badge_color}")
                t.append("  ")
                t.append(label, style=f"bold {_C_TEXT}")
                if subtext:
                    t.append(f"  ↳ {subtext}", style=_C_ACCENT)
                t.append(f"  {_fmt_timing(int(elapsed_step * 1000))}", style=_C_WARNING)
                yield t

            # Divider + footer
            yield Text("")
            yield Text("┄" * options.max_width, style=_C_BORDER)

            elapsed_total = now - d._t0
            ft = Text()
            ft.append(" ● ", style=f"bold {_C_PRIMARY}")
            ft.append(f"{d._current_phase}  ", style=f"bold {_C_TEXT_DIM}")
            ft.append(f"{_elapsed_hms(elapsed_total)}  ", style=_C_TEXT_DIM)
            if d._model:
                ft.append(f"{d._model}  ", style=_C_TEXT_DIM)
            ft.append(f"{d._mode}  ", style=_C_TEXT_DIM)
            ft.append("esc to cancel", style=f"dim {_C_TEXT_DIM}")
            yield ft


class _EventLogDisplay:
    """Rich Live-backed animated event log. One instance per investigation."""

    def __init__(self, model: str = "", mode: str = "local") -> None:
        from rich.live import Live

        global _live_console, _active_display

        self._model = model
        self._mode = mode
        self._t0 = time.monotonic()
        self._completed: list[Text] = []
        self._active_steps: dict[str, dict] = {}  # node_name → {t0, subtext, subtext_until}
        self._current_phase = "LOAD"
        self._lock = threading.Lock()

        self._console = Console(highlight=False)
        self._live = Live(
            _LiveRenderable(self),
            console=self._console,
            refresh_per_second=10,
            auto_refresh=True,
        )
        self._live.start(refresh=True)
        _live_console = self._console
        _active_display = self

    def stop(self) -> None:
        global _live_console, _active_display
        if self._live.is_started:
            self._live.stop()
        if _live_console is self._console:
            _live_console = None
        if _active_display is self:
            _active_display = None

    def step_start(self, node_name: str) -> None:
        with self._lock:
            self._active_steps[node_name] = {
                "t0": time.monotonic(),
                "subtext": None,
                "subtext_until": 0.0,
            }
            self._current_phase = _node_phase_label(node_name)

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        with self._lock:
            self._active_steps.pop(node_name, None)
            elapsed_total = time.monotonic() - self._t0
            ev_type = _node_event_type(node_name)
            badge_label, badge_color = _BADGE_STYLES.get(ev_type, ("DIAG  ", _C_ORANGE))
            label = _node_label(node_name)
            err = event.status == "error"
            msg = _humanise_message(event.message or "")
            timing = _fmt_timing(event.elapsed_ms)

            t = Text()
            t.append(f"{_elapsed_hms(elapsed_total)}  ", style=_C_TEXT_DIM)
            t.append("✗  " if err else "✓  ", style=f"bold {_C_ERROR if err else _C_PRIMARY}")
            t.append(f"[{badge_label}]", style=f"bold {badge_color}")
            t.append("  ")
            t.append(label, style=f"bold {_C_TEXT}")
            if msg:
                t.append(f"  {msg}", style=_C_ACCENT)
            t.append(f"  {timing}", style=_C_TEXT_DIM)
            self._completed.append(t)

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        with self._lock:
            if node_name in self._active_steps:
                self._active_steps[node_name]["subtext"] = text
                self._active_steps[node_name]["subtext_until"] = time.monotonic() + duration


# ─────────────────────────────────────────────────────────────────────────────
# Progress event + tracker
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProgressEvent:
    node_name: str
    elapsed_ms: int
    fields_updated: list[str] = field(default_factory=list)
    status: str = "completed"
    message: str | None = None


class ProgressTracker:
    """Drives the event-log display from node lifecycle calls (start/complete/error)."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._start_times: dict[str, float] = {}
        self._rich = get_output_format() == "rich"
        self._display: _EventLogDisplay | None = None
        if self._rich:
            self._display = _EventLogDisplay()

    def start(self, node_name: str, message: str | None = None) -> None:
        self._start_times[node_name] = time.monotonic()
        self.events.append(
            ProgressEvent(node_name=node_name, elapsed_ms=0, status="started", message=message)
        )
        if self._rich:
            if node_name == "publish_findings":
                # Stop the animated display so the final report prints cleanly below
                if self._display:
                    self._display.stop()
                    self._display = None
            elif self._display:
                self._display.step_start(node_name)
        else:
            print(f"  … {_node_label(node_name)}")

    def complete(
        self, node_name: str, fields_updated: list[str] | None = None, message: str | None = None
    ) -> None:
        self._finish(node_name, "completed", fields_updated or [], message)

    def error(self, node_name: str, message: str) -> None:
        self._finish(node_name, "error", [], message)

    def update_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        """Push a live status string into the active spinner for *node_name*."""
        if self._display:
            self._display.step_subtext(node_name, text, duration)

    def _finish(
        self, node_name: str, status: str, fields_updated: list[str], message: str | None
    ) -> None:
        elapsed_ms = int(
            (time.monotonic() - self._start_times.pop(node_name, time.monotonic())) * 1000
        )
        event = ProgressEvent(
            node_name=node_name,
            elapsed_ms=elapsed_ms,
            fields_updated=fields_updated,
            status=status,
            message=message,
        )
        self.events.append(event)

        if self._rich:
            if self._display:
                self._display.step_complete(node_name, event)
            else:
                # Display was stopped (publish_findings path) — print a plain Rich line
                mark = "✗" if status == "error" else "●"
                line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
                if msg := _humanise_message(message or ""):
                    line += f"  {msg}"
                Console(highlight=False).print(line)
            return

        # text mode
        mark = "✗" if status == "error" else "●"
        line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
        if msg := _humanise_message(message or ""):
            line += f"  {msg}"
        print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton tracker
# ─────────────────────────────────────────────────────────────────────────────

_tracker: ProgressTracker | None = None


def get_tracker(*, reset: bool = False) -> ProgressTracker:
    global _tracker
    if _tracker is None or reset:
        if reset and _tracker is not None and _tracker._display:
            _tracker._display.stop()
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    """Kept for backward compatibility with existing call sites."""
    return get_tracker(reset=True)


# ─────────────────────────────────────────────────────────────────────────────
# Investigation header
# ─────────────────────────────────────────────────────────────────────────────


def render_investigation_header(
    alert_name: str, pipeline_name: str, severity: str, alert_id: str | None = None
) -> None:
    sev_color = "red" if severity.lower() == "critical" else "yellow"
    fields = [
        ("  Alert      ", alert_name, "bold white"),
        ("  Pipeline   ", pipeline_name, "cyan"),
        ("  Severity   ", severity, f"bold {sev_color}"),
    ]
    if alert_id:
        fields.append(("  Alert ID   ", alert_id, "dim"))

    if get_output_format() == "rich":
        console = _get_console()
        console.print()
        for label, value, style in fields:
            console.print(Text.assemble((label, "dim"), (value, style)))
        console.print()
    else:
        print()
        for label, value, _ in fields:
            print(f"{label}{value}")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Debug output
# ─────────────────────────────────────────────────────────────────────────────


def _is_verbose() -> bool:
    if os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes"):
        return True
    try:
        from app.cli.support.context import is_debug, is_verbose

        return is_verbose() or is_debug()
    except Exception:
        return False


def debug_print(message: str) -> None:
    if not _is_verbose():
        return
    if get_output_format() == "rich":
        _get_console().print(f"[dim]{message}[/]")
    else:
        print(f"DEBUG: {message}")
