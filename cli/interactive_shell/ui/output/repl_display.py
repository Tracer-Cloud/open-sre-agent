from __future__ import annotations

import io
import os
import sys
import threading
import time
from typing import Any

from rich.console import Console
from rich.text import Text

from cli.interactive_shell.ui.output.console_state import (
    _capture_footer_snapshot,
    get_prompt_suppress_fn,
)
from cli.interactive_shell.ui.output.events import ProgressEvent
from cli.interactive_shell.ui.output.labels import (
    _node_phase_label,
    build_progress_step_text,
)
from cli.interactive_shell.ui.theme import BRAND, DIM, SECONDARY
from cli.interactive_shell.ui.time_format import _elapsed_hms

_REPL_ANIM_FRAMES = ("·", "··", "···", "··")
_REPL_ANIM_INTERVAL = 0.35
_ANIM_SEC = "\x1b[38;5;247m"
_ANIM_DIM = "\x1b[2m"
_ANIM_RST = "\x1b[0m"


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

    def stop(self) -> None:
        self._stop_animation()
        _capture_footer_snapshot(self)

    def _emit(self, line: Text | Any) -> None:
        self._stop_animation()
        from cli.interactive_shell.ui.choice_menu import prepare_repl_output_line

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
        stop = threading.Event()
        self._anim_stop = stop
        t0 = self._t0

        def _run() -> None:
            frame_idx = 0
            while not stop.wait(_REPL_ANIM_INTERVAL):
                frame_idx = (frame_idx + 1) % len(_REPL_ANIM_FRAMES)
                dots = _REPL_ANIM_FRAMES[frame_idx]
                ts = _elapsed_hms(time.monotonic() - t0)
                frame_str = (
                    f"\033[A\r"
                    f"{_ANIM_SEC}{ts}  {_ANIM_RST}"
                    f"{_ANIM_DIM}      ↳  {_ANIM_RST}"
                    f"{_ANIM_SEC}{prefix} {dots}{_ANIM_RST}"
                    f"\033[K\n"
                )
                if not stop.is_set():
                    sys.stdout.write(frame_str)
                    sys.stdout.flush()

        thread = threading.Thread(target=_run, daemon=True)
        self._anim_thread = thread
        thread.start()

    def animate_hint(self, text: str) -> None:
        self._stop_animation()
        prefix = text.rstrip("· \t")
        elapsed_total = time.monotonic() - self._t0
        t = Text()
        t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
        t.append("      ↳  ", style=DIM)
        t.append(f"{prefix} ·", style=SECONDARY)
        self._emit(t)
        self._start_animation(prefix)

    def step_start(self, node_name: str) -> None:
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

        from cli.interactive_shell.ui.theme import MARKDOWN_THEME

        with self._console.use_theme(MARKDOWN_THEME):
            self._emit(Markdown(text, code_theme="ansi_dark"))

    def print_above_renderable(self, renderable: Any) -> None:
        self._emit(renderable)
