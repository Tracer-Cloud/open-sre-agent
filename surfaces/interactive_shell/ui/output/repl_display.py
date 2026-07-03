from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import time
from typing import Any

from rich.console import Console
from rich.text import Text

from platform.terminal.theme import BRAND, DIM, SECONDARY
from surfaces.interactive_shell.ui.components.time_format import _elapsed_hms
from surfaces.interactive_shell.ui.output.events import ProgressEvent
from surfaces.interactive_shell.ui.output.labels import (
    _node_phase_label,
    build_progress_step_text,
)

_REPL_ANIM_FRAMES = ("·", "··", "···", "··")
_REPL_ANIM_INTERVAL = 0.35
# Timestamp + indent + trailing dot animation; keep hints on one physical row.
_HINT_LINE_OVERHEAD = 20


def _terminal_columns() -> int:
    try:
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        cols = 80
    return max(40, cols - 1)


def _fit_hint_prefix(prefix: str, *, cols: int | None = None) -> str:
    """Truncate hint text so cursor-up animation stays on a single terminal row."""
    budget = max(16, (cols if cols is not None else _terminal_columns()) - _HINT_LINE_OVERHEAD)
    if len(prefix) <= budget:
        return prefix
    if budget <= 3:
        return prefix[:budget]
    return f"{prefix[: budget - 3]}..."


def _stdout_is_tty() -> bool:
    try:
        return os.isatty(sys.stdout.fileno())
    except (AttributeError, io.UnsupportedOperation, OSError):
        return False


class _ReplEventLogDisplay:
    """Append-only investigation progress for the interactive REPL."""

    def __init__(self, model: str = "", mode: str = "local", t0: float | None = None) -> None:
        self._model = model
        self._mode = mode
        self._t0 = t0 if t0 is not None else time.monotonic()
        self._active_steps: dict[str, dict[str, Any]] = {}
        self._current_phase = "LOAD"
        self._lock = threading.Lock()
        self._console = Console(highlight=False)
        self._prompt_suppressed = False
        self._anim_stop: threading.Event | None = None
        self._anim_thread: threading.Thread | None = None
        self._last_emitted_hint: str | None = None

    def stop(self) -> None:
        from surfaces.interactive_shell.ui.output.console_state import _capture_footer_snapshot

        self._stop_animation()
        _capture_footer_snapshot(self)

    def _emit(self, line: Text | Any) -> None:
        self._stop_animation()
        from surfaces.interactive_shell.ui.components.choice_menu import prepare_repl_output_line

        prepare_repl_output_line()
        self._console.print(line)

    def _stop_animation(self) -> None:
        stop = self._anim_stop
        if stop is not None:
            stop.set()
            self._anim_stop = None
        thread = self._anim_thread
        if thread is not None:
            thread.join(timeout=0.3)
            self._anim_thread = None

    def _start_animation(self, prefix: str) -> None:
        if not _stdout_is_tty():
            return
        fitted_prefix = _fit_hint_prefix(prefix)
        stop = threading.Event()
        self._anim_stop = stop
        t0 = self._t0

        def _run() -> None:
            from platform.terminal import theme

            frame_idx = 0
            while not stop.wait(_REPL_ANIM_INTERVAL):
                frame_idx = (frame_idx + 1) % len(_REPL_ANIM_FRAMES)
                dots = _REPL_ANIM_FRAMES[frame_idx]
                ts = _elapsed_hms(time.monotonic() - t0)
                frame_str = (
                    f"\033[A\r"
                    f"{theme.SECONDARY_ANSI}{ts}  {theme.ANSI_RESET}"
                    f"{theme.ANSI_DIM}      ↳  {theme.ANSI_RESET}"
                    f"{theme.SECONDARY_ANSI}{fitted_prefix} {dots}{theme.ANSI_RESET}"
                    f"\033[K\n"
                )
                if not stop.is_set():
                    sys.stdout.write(frame_str)
                    sys.stdout.flush()

        thread = threading.Thread(target=_run, daemon=True)
        self._anim_thread = thread
        thread.start()

    def animate_hint(self, text: str) -> None:
        """Print one compact lap-status line; no cursor-up animation in the REPL.

        Append-only output under prompt_toolkit cannot safely rewrite rows in
        place — the old animation thread spammed stdout and looked like hundreds
        of repeated API/tool lines when hints wrapped.
        """
        prefix = _fit_hint_prefix(text.rstrip("· \t"))
        if prefix == self._last_emitted_hint:
            return
        self._last_emitted_hint = prefix
        elapsed_total = time.monotonic() - self._t0
        t = Text()
        t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
        t.append("      ↳  ", style=DIM)
        t.append(prefix, style=SECONDARY)
        self._emit(t)

    def step_start(self, node_name: str) -> None:
        from surfaces.interactive_shell.ui.output.console_state import get_prompt_suppress_fn

        prompt_suppress_fn = get_prompt_suppress_fn()
        if not self._prompt_suppressed and prompt_suppress_fn is not None:
            self._prompt_suppressed = True
            prompt_suppress_fn()
        with self._lock:
            self._active_steps[node_name] = {
                "t0": time.monotonic(),
                "subtext": None,
                "subtext_until": 0.0,
            }
            self._current_phase = _node_phase_label(node_name)
        self._last_emitted_hint = None
        self._emit(
            build_progress_step_text(
                node_name=node_name,
                elapsed_total=time.monotonic() - self._t0,
                status="active",
            )
        )

    def set_tool_details(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        pass

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        self._stop_animation()
        self._last_emitted_hint = None
        with self._lock:
            info = self._active_steps.pop(node_name, {})
            subtext = info.get("subtext")
        line = build_progress_step_text(
            node_name=node_name,
            elapsed_total=time.monotonic() - self._t0,
            elapsed_step_ms=event.elapsed_ms,
            status=event.status,
            message=event.message,
        )
        if subtext:
            line.append(f"  ↳ {subtext}", style=BRAND)
        self._emit(line)

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        if not text.strip():
            return
        with self._lock:
            if node_name in self._active_steps:
                self._active_steps[node_name]["subtext"] = text
                self._active_steps[node_name]["subtext_until"] = time.monotonic() + duration

    def print_above(self, text: str) -> None:
        if not text.strip():
            return
        from rich.markdown import Markdown

        from platform.terminal.theme import MARKDOWN_THEME

        with self._console.use_theme(MARKDOWN_THEME):
            self._emit(Markdown(text, code_theme="ansi_dark"))

    def print_above_renderable(self, renderable: Any) -> None:
        self._emit(renderable)
