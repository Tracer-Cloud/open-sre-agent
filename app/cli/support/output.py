"""
Output utilities shared across nodes.

- Typed event-log renderer: render_event(), render_footer(), render_divider()
- ProgressTracker: thin wrapper that drives the event log from node lifecycle calls
- Investigation header display
- Debug output (verbose mode)
- Environment detection (rich TTY vs plain text)
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from app.cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from app.tools.registry import get_registered_tool_map, resolve_tool_display_name
from app.utils.tool_trace import format_json_preview

try:
    import select
    import termios
except ImportError:  # pragma: no cover - Windows fallback
    select = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

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


def _is_silent_output() -> bool:
    """Return whether output rendering is explicitly disabled."""
    return get_output_format() == "none"


def _safe_print(text: str) -> None:
    """Print text, replacing unencodable characters (e.g. on Windows cp1252)."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        with contextlib.suppress(BrokenPipeError):
            print(text.encode(enc, errors="replace").decode(enc))
    except BrokenPipeError:
        # Downstream pipe/consumer closed (e.g. piping to `head`); ignore to avoid noisy traceback.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Badge registry
# ─────────────────────────────────────────────────────────────────────────────

# (padded_label, text_color)  — all labels are 6 chars wide
_BADGE_STYLES: dict[str, tuple[str, str]] = {
    "READ": ("READ  ", HIGHLIGHT),
    "PLAN": ("PLAN  ", BRAND),
    "INVEST": ("INVEST", WARNING),
    "DIAG": ("DIAG  ", TEXT),
    "MERGE": ("MERGE ", SECONDARY),
}

_NODE_EVENT_TYPE: dict[str, str] = {
    "extract_alert": "READ",
    "resolve_integrations": "READ",
    "plan_actions": "PLAN",
    "merge_hypotheses": "MERGE",
    "investigation_agent": "INVEST",
    "diagnose_root_cause": "DIAG",
    "opensre_llm_eval": "DIAG",
    "publish_findings": "DIAG",
}

_NODE_PHASE: dict[str, str] = {
    "extract_alert": "LOAD",
    "resolve_integrations": "LOAD",
    "plan_actions": "PLAN",
    "merge_hypotheses": "DIAGNOSE",
    "investigation_agent": "INVESTIGATE",
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
    "investigation_agent": "Investigation",
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


def set_live_console(console: Console | None) -> None:
    """Register an active console globally for top-level routing."""
    global _live_console
    _live_console = console


def unregister_live_console(expected: Console | None) -> None:
    """Safely clear the active console registry ONLY if it matches the owner."""
    global _live_console
    if expected is not None and _live_console is expected:
        _live_console = None


def stop_display() -> None:
    """Stop any running live display. Call before printing final report output."""
    global _active_display
    if _active_display is not None:
        _active_display.stop()
    if "_tracker" in globals() and _tracker is not None:
        _tracker._stop_toggle_watcher()


# ─────────────────────────────────────────────────────────────────────────────
# Public rendering functions (spec: render_event, render_footer, render_divider)
# ─────────────────────────────────────────────────────────────────────────────


def render_divider(width: int = 80) -> None:
    """Print a DIM-coloured dashed ┄ divider."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        _get_console().print(Text("┄" * width, style=DIM))
    else:
        _safe_print("─" * width)


def render_footer(phase: str, elapsed: float, model: str, mode: str) -> None:
    """Print the persistent status footer line."""
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        t = Text()
        t.append(" ● ", style=f"bold {HIGHLIGHT}")
        t.append(f"{phase}  ", style=f"bold {SECONDARY}")
        t.append(f"{_elapsed_hms(elapsed)}  ", style=SECONDARY)
        if model:
            t.append(f"{model}  ", style=SECONDARY)
        t.append(f"{mode}  ", style=SECONDARY)
        t.append("esc to cancel", style=DIM)
        _get_console().print(t)
    else:
        _safe_print(f"● {phase}  {elapsed:.1f}s  {model}  {mode}")


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
    if _is_silent_output():
        return
    if get_output_format() == "rich":
        badge_label, badge_color = _BADGE_STYLES.get(event_type, ("DIAG  ", WARNING))
        ts = _elapsed_hms(elapsed_s)
        t = Text()
        t.append(f"{ts}  ", style=SECONDARY)
        if muted:
            t.append(f"{glyph}  ", style=SECONDARY)
            msg_style = SECONDARY
        elif error:
            t.append("✗  ", style=f"bold {ERROR}")
            msg_style = TEXT
        else:
            t.append(f"{glyph}  ", style=f"bold {HIGHLIGHT}")
            msg_style = TEXT
        t.append(f"[{badge_label}]", style=f"bold {badge_color}")
        t.append("  ")
        t.append(message, style=msg_style)
        if insight:
            t.append(f"  ↳ {insight}", style=BRAND)
        _get_console().print(t)
    else:
        mark = "✗" if error else ("·" if muted else "✓")
        line = f"  {mark}  [{event_type}]  {message}"
        if insight:
            line += f"  ↳ {insight}"
        _safe_print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Live event-log display
# ─────────────────────────────────────────────────────────────────────────────

_SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
_FRAME_SECS = 0.10
_TOOL_DETAIL_TOGGLE_BYTES = {b"\x0f", b"\x00"}  # ctrl+o; ctrl+0/space on some terminals


def _control_char(value: int, existing: Any) -> Any:
    """Return a termios control-char value matching the platform's cc entry type."""
    if isinstance(existing, bytes):
        return bytes([value])
    if isinstance(existing, str):
        return chr(value)
    return value


def _disable_control_char(fd: int, existing: Any) -> Any:
    """Return the platform's disabled control character value if available."""
    disabled = 0
    with contextlib.suppress(Exception):
        disabled = int(os.fpathconf(fd, "PC_VDISABLE"))
    if disabled < 0 or disabled > 255:
        disabled = 0
    return _control_char(disabled, existing)


class CtrlOToggleWatcher:
    """Background stdin watcher for Ctrl+O without triggering terminal output discard.

    On BSD/macOS terminals Ctrl+O is commonly bound to VDISCARD. A plain cbreak
    setup leaves that special character active, so pressing the toggle key can
    make the terminal stop displaying output. We keep ISIG enabled for Ctrl+C,
    but disable IEXTEN/VDISCARD so Ctrl+O reaches the application as a byte.
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._old_attrs: Any = None

    def start(self) -> None:
        if select is None or termios is None:
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return
        try:
            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            new_attrs = termios.tcgetattr(self._fd)
            new_attrs[3] &= ~(termios.ICANON | termios.ECHO)
            if hasattr(termios, "IEXTEN"):
                new_attrs[3] &= ~termios.IEXTEN
            if hasattr(termios, "VMIN"):
                new_attrs[6][termios.VMIN] = 1
            if hasattr(termios, "VTIME"):
                new_attrs[6][termios.VTIME] = 0
            if hasattr(termios, "VDISCARD"):
                index = termios.VDISCARD
                new_attrs[6][index] = _disable_control_char(self._fd, new_attrs[6][index])
            termios.tcsetattr(self._fd, termios.TCSADRAIN, new_attrs)
        except Exception:
            self._fd = None
            self._old_attrs = None
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
        if self._fd is not None and self._old_attrs is not None and termios is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)

    def _run(self) -> None:
        if self._fd is None or select is None:
            return
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self._fd], [], [], 0.1)
            except Exception:
                return
            if not readable:
                continue
            try:
                data = os.read(self._fd, 1)
            except Exception:
                return
            if data in _TOOL_DETAIL_TOGGLE_BYTES:
                self._callback()


def _tool_source_label(tool_name: str) -> str:
    tool = get_registered_tool_map().get(tool_name)
    source = str(tool.source) if tool is not None else _infer_tool_source(tool_name)
    if source == "grafana":
        return "Grafana"
    if source == "knowledge":
        return "SRE"
    if source == "openclaw":
        return "OpenClaw"
    return source.replace("_", " ").title() if source else "Tools"


def _infer_tool_source(tool_name: str) -> str:
    lowered = tool_name.lower()
    for source in ("grafana", "datadog", "cloudwatch", "sentry", "honeycomb", "openclaw"):
        if source in lowered:
            return source
    if lowered.startswith("get_sre_"):
        return "knowledge"
    return "tools"


def _tool_short_label(tool_name: str, source_label: str) -> str:
    display = resolve_tool_display_name(tool_name)
    label = display
    for prefix in (
        source_label,
        source_label.lower(),
        f"{source_label} ",
        f"{source_label.lower()} ",
        "query ",
        "get ",
    ):
        if label.startswith(prefix):
            label = label[len(prefix) :].strip()
    if source_label == "Grafana" and label.lower().startswith("grafana "):
        label = label[len("grafana ") :].strip()
    return label or display


class _LiveRenderable:
    """Rich renderable that rebuilds the event-log on every Live refresh.

    Only active (in-progress) steps are rendered here.  Completed steps are
    printed *above* the live region via ``console.print`` the moment they finish
    so they are never re-rendered — preventing the staircase scrollback bug
    where Rich under-counts live-area lines and fails to erase them fully.
    """

    def __init__(self, display: _EventLogDisplay) -> None:
        self._d = display

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        d = self._d
        now = time.monotonic()
        with d._lock:
            if d._tool_details_visible:
                yield from _render_tool_detail_view(d, options, now)
                return

            # Active step lines (animated).
            # Completed steps are NOT yielded here — they are printed permanently
            # above the live region in step_complete() to avoid the staircase bug.
            for node_name, info in d._active_steps.items():
                elapsed_step = now - info["t0"]
                elapsed_total = now - d._t0
                frame = _SPINNER_FRAMES[int(elapsed_step / _FRAME_SECS) % len(_SPINNER_FRAMES)]
                ev_type = _node_event_type(node_name)
                badge_label, badge_color = _BADGE_STYLES.get(ev_type, ("DIAG  ", WARNING))
                label = _node_label(node_name)

                # subtext (tool calls / reasoning snippets)
                subtext: str | None = info.get("subtext")
                if subtext and now > info.get("subtext_until", 0.0):
                    subtext = None

                t = Text()
                t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
                t.append(f"{frame}  ", style=SECONDARY)
                t.append(f"[{badge_label}]", style=f"bold {badge_color}")
                t.append("  ")
                t.append(label, style=f"bold {TEXT}")
                if subtext:
                    t.append(f"  ↳ {subtext}", style=BRAND)
                t.append(f"  {_fmt_timing(int(elapsed_step * 1000))}", style=WARNING)
                yield t

            # Divider + footer.
            # Use max_width - 1 so the line never hits the terminal edge exactly;
            # a full-width line often causes an implicit wrap that Rich doesn't
            # count, making it under-erase on the next refresh.
            yield Text("")
            yield Text("┄" * (options.max_width - 1), style=DIM)

            elapsed_total = now - d._t0
            ft = Text()
            ft.append(" ● ", style=f"bold {HIGHLIGHT}")
            ft.append(f"{d._current_phase}  ", style=f"bold {SECONDARY}")
            ft.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
            if d._model:
                ft.append(f"{d._model}  ", style=SECONDARY)
            ft.append(f"{d._mode}  ", style=SECONDARY)
            ft.append("ctrl+o tool details  ", style=DIM)
            ft.append("esc to cancel", style=DIM)
            yield ft


def _render_tool_detail_view(
    display: _EventLogDisplay,
    options: ConsoleOptions,
    now: float,
) -> RenderResult:
    elapsed_total = now - display._t0
    heading = Text()
    heading.append(" Tool Details", style=f"bold {TEXT}")
    summary = display._tool_summary
    if summary:
        heading.append(f"  {summary}", style=BRAND)
    yield heading

    records = display._tool_detail_records[-6:]
    hidden_count = max(0, len(display._tool_detail_records) - len(records))
    if hidden_count:
        yield Text(f"  {hidden_count} older tool call(s) hidden", style=DIM)
    if not records:
        yield Text("  No tool calls have finished yet.", style=DIM)

    for record in records:
        elapsed = str(record.get("elapsed") or "")
        suffix = f"  {elapsed}" if elapsed else ""
        row = Text()
        row.append("  ● ", style=f"bold {HIGHLIGHT}")
        row.append(str(record.get("display") or "tool"), style=f"bold {TEXT}")
        row.append(suffix, style=SECONDARY)
        yield row

        tool_input = record.get("input")
        output = record.get("output")
        if tool_input not in ({}, None):
            yield Text("    Input:", style=SECONDARY)
            for line in format_json_preview(tool_input, max_chars=1200).splitlines():
                yield Text(f"      {line}", style=DIM)
        if output not in ({}, None, ""):
            yield Text("    Output:", style=SECONDARY)
            for line in format_json_preview(output, max_chars=2200).splitlines():
                yield Text(f"      {line}", style=DIM)
        yield Text("")

    yield Text("┄" * (options.max_width - 1), style=DIM)
    footer = Text()
    footer.append(" ● ", style=f"bold {HIGHLIGHT}")
    footer.append("TOOL DETAILS  ", style=f"bold {SECONDARY}")
    footer.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
    if display._model:
        footer.append(f"{display._model}  ", style=SECONDARY)
    footer.append(f"{display._mode}  ", style=SECONDARY)
    footer.append("ctrl+o compact view  ", style=DIM)
    footer.append("esc to cancel", style=DIM)
    yield footer


class _EventLogDisplay:
    """Rich Live-backed animated event log. One instance per investigation."""

    def __init__(self, model: str = "", mode: str = "local", t0: float | None = None) -> None:
        from rich.live import Live

        global _live_console, _active_display

        self._model = model
        self._mode = mode
        self._t0 = t0 if t0 is not None else time.monotonic()
        self._active_steps: dict[str, dict] = {}  # node_name → {t0, subtext, subtext_until}
        self._current_phase = "LOAD"
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._tool_summary = ""
        self._lock = threading.Lock()

        self._console = Console(highlight=False)
        self._live = Live(
            _LiveRenderable(self),
            console=self._console,
            refresh_per_second=10,
            auto_refresh=True,
            # Clip the live area to the terminal height so Rich never tries to
            # scroll back past more lines than it rendered.
            vertical_overflow="ellipsis",
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

    def set_tool_details(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        with self._lock:
            self._tool_details_visible = visible
            self._tool_detail_records = list(records)
            self._tool_summary = summary
        if self._live.is_started:
            if clear:
                self._live.console.clear()
            self._live.refresh()

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        # Compute elapsed before entering the lock so the timestamp is as
        # accurate as possible even if the lock is briefly contended.
        elapsed_total = time.monotonic() - self._t0
        with self._lock:
            self._active_steps.pop(node_name, None)
            ev_type = _node_event_type(node_name)
            badge_label, badge_color = _BADGE_STYLES.get(ev_type, ("DIAG  ", WARNING))
            label = _node_label(node_name)
            err = event.status == "error"
            msg = _humanise_message(event.message or "")
            timing = _fmt_timing(event.elapsed_ms)

            t = Text()
            t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
            t.append("✗  " if err else "✓  ", style=f"bold {ERROR if err else HIGHLIGHT}")
            t.append(f"[{badge_label}]", style=f"bold {badge_color}")
            t.append("  ")
            t.append(label, style=f"bold {TEXT}")
            if msg:
                t.append(f"  {msg}", style=BRAND)
            t.append(f"  {timing}", style=SECONDARY)

        # Print the completed line permanently *above* the live region.
        # This must happen outside _lock: the auto-refresh thread holds
        # Rich's internal _refresh_lock while calling __rich_console__ (which
        # acquires _lock), so printing under _lock would deadlock.
        #
        # ``step_complete`` can be invoked from a background pipeline thread
        # concurrently with ``stop()``; once the live region has been torn down
        # the line would otherwise leak out *below* whatever ``stop()`` already
        # flushed, leaving a stray completed-step row after the display closes.
        if self._live.is_started:
            self._live.console.print(t)

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        with self._lock:
            if node_name in self._active_steps:
                self._active_steps[node_name]["subtext"] = text
                self._active_steps[node_name]["subtext_until"] = time.monotonic() + duration

    def print_above(self, text: str) -> None:
        """Print text permanently above the live region via the Live's own console."""
        if not text.strip():
            return
        from rich.markdown import Markdown

        from app.cli.interactive_shell.ui.theme import MARKDOWN_THEME

        with self._live.console.use_theme(MARKDOWN_THEME):
            self._live.console.print(Markdown(text, code_theme="ansi_dark"))

    def print_above_renderable(self, renderable: Any) -> None:
        """Print a rich renderable permanently above the live region."""
        self._live.console.print(renderable)


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
        self._t0: float = time.monotonic()
        self._silent = _is_silent_output()
        self._rich = get_output_format() == "rich"
        self._display: _EventLogDisplay | None = None
        self._tool_start_times: dict[str, float] = {}
        self._tool_inputs: dict[str, Any] = {}
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._printed_tool_detail_ids: set[int] = set()
        self._tool_summary_counts: dict[str, dict[str, int]] = {}
        self._tool_summary_order: list[tuple[str, str]] = []
        self._toggle_watcher: CtrlOToggleWatcher | None = None
        if self._rich and not self._silent:
            self._display = _EventLogDisplay(t0=self._t0)
            self._toggle_watcher = CtrlOToggleWatcher(self.toggle_tool_details)
            self._toggle_watcher.start()

    @property
    def has_active_display(self) -> bool:
        """Return True if the live display is currently running."""
        return self._display is not None

    def stop(self) -> None:
        """Stop the active live display if running."""
        self._stop_toggle_watcher()
        if self._display:
            self._display.stop()
            self._display = None

    def _stop_toggle_watcher(self) -> None:
        if self._toggle_watcher is not None:
            self._toggle_watcher.stop()
            self._toggle_watcher = None

    def start(self, node_name: str, message: str | None = None) -> None:
        self._start_times[node_name] = time.monotonic()
        self.events.append(
            ProgressEvent(node_name=node_name, elapsed_ms=0, status="started", message=message)
        )
        if self._silent:
            return
        if self._rich:
            if node_name == "publish_findings":
                # Stop the animated display so the final report prints cleanly below
                self._stop_toggle_watcher()
                if self._display:
                    self._display.stop()
                    self._display = None
            else:
                if self._display is None:
                    self._display = _EventLogDisplay(t0=self._t0)
                self._display.step_start(node_name)
        else:
            _safe_print(f"  … {_node_label(node_name)}")

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

    def print_above(self, text: str) -> None:
        """Print text permanently above the active live region, or to stdout in text mode."""
        if self._silent:
            return
        if self._display:
            self._display.print_above(text)
        elif text.strip():
            for line in text.strip().splitlines():
                print(f"  {line}")

    def print_above_renderable(self, renderable: Any) -> None:
        """Print a rich renderable permanently above the active live region, or to console."""
        if self._display:
            self._display.print_above_renderable(renderable)
        else:
            _get_console().print()
            _get_console().print(renderable)

    def set_tool_detail_view(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        """Replace the live progress area with a transient tool-detail view."""
        if self._display:
            self._display.set_tool_details(
                visible=visible,
                records=records,
                summary=summary,
                clear=clear,
            )

    def record_tool_start(
        self,
        tool_name: str,
        tool_input: Any = None,
        *,
        event_key: str | None = None,
    ) -> None:
        """Record a tool start for compact live summary and optional details."""
        if self._silent:
            return
        key = event_key or tool_name
        self._tool_start_times[key] = time.monotonic()
        self._tool_inputs[key] = tool_input
        self._record_tool_summary(tool_name)
        self._update_tool_summary_subtext()
        self._sync_tool_detail_view()

    def record_tool_end(
        self,
        tool_name: str,
        output: Any = None,
        *,
        event_key: str | None = None,
        tool_input: Any = None,
    ) -> None:
        """Record a tool end and buffer detailed input/output for Ctrl+O."""
        if self._silent:
            return
        key = event_key or tool_name
        start = self._tool_start_times.pop(key, None)
        elapsed = f"{int((time.monotonic() - start) * 1000)}ms" if start is not None else ""
        stored_input = self._tool_inputs.pop(key, None)
        self._update_tool_summary_subtext()
        self._record_tool_detail(
            resolve_tool_display_name(tool_name),
            tool_input if tool_input is not None else stored_input,
            output,
            elapsed=elapsed,
        )

    def toggle_tool_details(self) -> None:
        """Toggle the live view between compact progress and tool details."""
        if self._silent:
            return
        self._tool_details_visible = not self._tool_details_visible
        if self._rich and self._display:
            self._sync_tool_detail_view(clear=True)
            return
        label = "shown" if self._tool_details_visible else "hidden"
        _safe_print(f"  Tool details {label} (ctrl+o)")
        if self._tool_details_visible:
            self._flush_tool_details()

    def _sync_tool_detail_view(self, *, clear: bool = False) -> None:
        if self._rich and self._display:
            self.set_tool_detail_view(
                visible=self._tool_details_visible,
                records=self._tool_detail_records,
                summary=self.format_tool_summary(),
                clear=clear,
            )

    def _record_tool_summary(self, tool_name: str) -> None:
        source = _tool_source_label(tool_name)
        label = _tool_short_label(tool_name, source)
        source_counts = self._tool_summary_counts.setdefault(source, {})
        if label not in source_counts:
            self._tool_summary_order.append((source, label))
        source_counts[label] = source_counts.get(label, 0) + 1

    def _update_tool_summary_subtext(self) -> None:
        summary = self.format_tool_summary()
        if not summary:
            return
        self.update_subtext("investigation_agent", summary, duration=30.0)
        self.update_subtext("investigate", summary, duration=30.0)

    def format_tool_summary(self) -> str:
        source_labels: dict[str, list[str]] = {}
        for source, label in self._tool_summary_order:
            count = self._tool_summary_counts.get(source, {}).get(label, 0)
            if count <= 0:
                continue
            rendered = f"{label} x{count}" if count > 1 else label
            source_labels.setdefault(source, []).append(rendered)
        parts = [
            f"{source}: {', '.join(labels[:4])}{', ...' if len(labels) > 4 else ''}"
            for source, labels in source_labels.items()
        ]
        summary = " | ".join(parts[:2])
        return summary[:117] + "..." if len(summary) > 120 else summary

    def _record_tool_detail(
        self,
        display: str,
        tool_input: Any,
        output: Any,
        *,
        elapsed: str = "",
    ) -> None:
        if tool_input in ({}, None) and output in ({}, None, ""):
            return
        record = {
            "display": display,
            "input": tool_input,
            "output": output,
            "elapsed": elapsed,
        }
        self._tool_detail_records.append(record)
        if self._tool_details_visible:
            if self._rich and self._display:
                self._sync_tool_detail_view()
            else:
                self._print_tool_detail(record)

    def _flush_tool_details(self) -> None:
        for record in self._tool_detail_records:
            if id(record) not in self._printed_tool_detail_ids:
                self._print_tool_detail(record)

    def _print_tool_detail(self, record: dict[str, Any]) -> None:
        display = str(record.get("display") or "tool")
        tool_input = record.get("input")
        output = record.get("output")
        body_parts: list[str] = []
        if tool_input not in ({}, None):
            body_parts.append(f"Input:\n{format_json_preview(tool_input, max_chars=1600)}")
        if output not in ({}, None, ""):
            body_parts.append(f"Output:\n{format_json_preview(output, max_chars=3000)}")
        body = "\n\n".join(body_parts)
        elapsed = str(record.get("elapsed") or "")
        suffix = f"  {elapsed}" if elapsed else ""
        if self._rich:
            detail = Text()
            detail.append(f"  Tool details: {display}{suffix}\n", style=f"bold {TEXT}")
            for line in body.splitlines():
                detail.append(f"    {line}\n", style=DIM)
            self.print_above_renderable(detail)
        else:
            _safe_print(f"  Tool details: {display}{suffix}")
            for line in body.splitlines():
                _safe_print(f"      {line}")
        self._printed_tool_detail_ids.add(id(record))

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
        if self._silent:
            return

        if self._rich:
            if self._display:
                self._display.step_complete(node_name, event)
            else:
                # Display was stopped (e.g. diagnose path) — route safely above active live console
                mark = "✗" if status == "error" else "●"
                line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
                if msg := _humanise_message(message or ""):
                    line += f"  {msg}"
                self.print_above_renderable(line)
            return

        # text mode
        mark = "✗" if status == "error" else "●"
        line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
        if msg := _humanise_message(message or ""):
            line += f"  {msg}"
        _safe_print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton tracker
# ─────────────────────────────────────────────────────────────────────────────

_tracker: ProgressTracker | None = None


def get_tracker(*, reset: bool = False) -> ProgressTracker:
    global _tracker
    if _tracker is None or reset:
        if reset and _tracker is not None:
            _tracker.stop()
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    """Kept for backward compatibility with existing call sites."""
    return get_tracker(reset=True)


def set_silent_tracker() -> None:
    """Install a silent (no-display) tracker as the global singleton.

    Used by astream_investigation before starting the background thread so
    internal pipeline calls to tracker.start/complete don't open their own
    Rich Live display — the StreamRenderer drives the display instead.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Investigation header
# ─────────────────────────────────────────────────────────────────────────────


def render_investigation_header(
    alert_name: str, pipeline_name: str, severity: str, alert_id: str | None = None
) -> None:
    sev_color = ERROR if severity.lower() == "critical" else WARNING
    fields = [
        ("  Alert      ", alert_name, f"bold {TEXT}"),
        ("  Pipeline   ", pipeline_name, BRAND),
        ("  Severity   ", severity, f"bold {sev_color}"),
    ]
    if alert_id:
        fields.append(("  Alert ID   ", alert_id, SECONDARY))

    if get_output_format() == "rich":
        console = _get_console()
        console.print()
        for label, value, style in fields:
            console.print(Text.assemble((label, SECONDARY), (value, style)))
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
        _get_console().print(f"[{SECONDARY}]{message}[/]")
    else:
        print(f"DEBUG: {message}")
