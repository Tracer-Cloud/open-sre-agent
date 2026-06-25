"""Live Markdown renderer for diagnose-node token streams."""

from __future__ import annotations

import sys
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.ui.output import (
    ProgressTracker,
    _repl_progress_active,
    get_output_format,
    set_live_console,
    stop_display,
    unregister_live_console,
)
from app.cli.ui.renderer.constants import (
    _BOLD,
    _DIAGNOSE_LIVE_REFRESH,
    _DIAGNOSE_NODE,
    _DIAGNOSE_RENDER_INTERVAL_S,
    _DIAGNOSE_SPINNER_COLOR,
    _DIAGNOSE_SPINNER_NAME,
    _DIM,
    _GREEN,
    _RESET,
    _WHITE,
    _render_source,
)
from app.core.domain.stream import StreamEvent


class _DiagnoseStreamRenderer:
    """Owns the diagnose-node live-streaming state machine.

    Encapsulates the buffer of incoming token deltas, the lazy Rich Console
    + Live region, and the throttled Markdown re-parse cadence. Exists so
    :class:`StreamRenderer` keeps a single responsibility (event dispatch
    + node lifecycle + final report) while diagnose-specific streaming
    concerns live in one focused place.

    Lifecycle: :meth:`start` → :meth:`append_chunk` (per token-delta event)
    → :meth:`finish`. The same instance can be reused across multiple
    investigation runs — :meth:`start` resets all state.
    """

    def __init__(
        self,
        console: Console | None = None,
        tracker: ProgressTracker | None = None,
        *,
        local: bool = False,
    ) -> None:
        self.buffer: list[str] = []
        self._live: Live | None = None
        self._started: float = 0.0
        # Last time we re-rendered ``Markdown(buffer)`` into the Live region.
        # Throttled to ``_DIAGNOSE_RENDER_INTERVAL_S`` so long streams don't
        # incur O(n²) parsing.
        self._last_render: float = 0.0
        self._console: Console | None = console
        self._tracker: ProgressTracker | None = tracker
        self._local = local

    @property
    def streamed(self) -> bool:
        """True if any chunks were buffered during the run.

        Callers (specifically :meth:`StreamRenderer._print_report`) use this
        to decide whether the final ``Root Cause`` summary should be
        suppressed — it would duplicate text the user just watched stream.
        """
        return bool(self.buffer)

    def start(self) -> None:
        """Reset state and open the Live region (rich) or print a placeholder (text)."""
        self.buffer = []
        self._started = time.monotonic()
        # 0.0 sentinel forces the first chunk past the throttle gate so the
        # user sees something rendered as soon as tokens arrive.
        self._last_render = 0.0

        if _repl_progress_active():
            return

        if get_output_format() != "rich":
            sys.stdout.write(f"  … {_DIAGNOSE_NODE}\n")
            sys.stdout.flush()
            return

        if self._console is None:
            self._console = Console(highlight=False)
        spinner = Spinner(
            _DIAGNOSE_SPINNER_NAME,
            text=Text(
                f"{_DIAGNOSE_NODE}  reasoning…",
                style=f"bold {_DIAGNOSE_SPINNER_COLOR}",
            ),
            style=f"bold {_DIAGNOSE_SPINNER_COLOR}",
        )
        self._live = Live(
            spinner,
            console=self._console,
            refresh_per_second=_DIAGNOSE_LIVE_REFRESH,
            transient=False,
        )

        # Shrink the gap: stop previous display immediately before starting new one
        if self._tracker is not None:
            self._tracker.stop()
        else:
            stop_display()

        # Register console globally so that print_above_renderable fallbacks
        # correctly print above this live region during the diagnose phase.
        set_live_console(self._console)
        self._live.start()

    def append_chunk(self, event: StreamEvent) -> None:
        """Append a token delta to the buffer; refresh the Live region (throttled).

        The chunk's ``content`` shape varies by provider: OpenAI emits a
        plain string; some Anthropic SDK paths emit a list of content blocks.
        :func:`_flatten_chunk_content` handles both — calling ``str()`` on
        the list shape would render its Python repr instead of reasoning.
        """
        chunk = event.data.get("data", {}).get("chunk", {})
        content = chunk.get("content", "") if isinstance(chunk, dict) else ""
        if not content:
            return
        text = _flatten_chunk_content(content)
        if not text:
            return
        self.buffer.append(text)
        if len(self.buffer) == 1:
            latency_ms = (time.monotonic() - self._started) * 1000
            get_analytics().capture(
                Event.INVESTIGATION_FIRST_HYPOTHESIS_RENDERED,
                {
                    "latency_ms": int(latency_ms),
                    "stage": _DIAGNOSE_NODE,
                    "source": _render_source(local=self._local),
                },
            )
        if self._live is None:
            if _repl_progress_active() and self._tracker is not None:
                preview = "".join(self.buffer)
                if len(preview) > 80:
                    preview = "…" + preview[-77:]
                self._tracker.update_subtext(_DIAGNOSE_NODE, preview, duration=30.0)
            return
        # Throttle Markdown re-parse to once per refresh window; the final
        # flush in :meth:`finish` guarantees the latest buffer is rendered
        # before the Live region closes.
        now = time.monotonic()
        if now - self._last_render >= _DIAGNOSE_RENDER_INTERVAL_S:
            self._live.update(Markdown("".join(self.buffer)))
            self._last_render = now

    def finish(self, message: str | None = None) -> None:
        """Close the Live region (or text-mode flush) and print the resolved-dot line.

        ``message`` is appended dim-styled to the resolution line — typically
        a validity-score summary built by ``_build_node_message``.
        """
        elapsed = time.monotonic() - self._started

        if self._live is not None:
            # Final flush: any chunks pending in the last throttle window
            # render here so the user sees the complete reasoning.
            if self.buffer:
                self._live.update(Markdown("".join(self.buffer)))
            try:
                self._live.stop()
            finally:
                self._live = None
                # Unregister only if we own it (safeguard against subsequent activations)
                unregister_live_console(self._console)
            sys.stdout.write(
                f"  {_GREEN}●{_RESET}  {_BOLD}{_WHITE}{_DIAGNOSE_NODE}{_RESET}"
                f"  {_DIM}{elapsed:.1f}s{_RESET}"
            )
            if message:
                sys.stdout.write(f"  {_DIM}{message}{_RESET}")
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            if self.buffer:
                for line in "".join(self.buffer).strip().splitlines():
                    print(f"  {line}")
            tail = f"  ● {_DIAGNOSE_NODE}  {elapsed:.1f}s"
            if message:
                tail += f"  {message}"
            print(tail)


def _flatten_chunk_content(content: Any) -> str:
    """Resolve a chat-model chunk's ``content`` to plain text.

    OpenAI emits a string. Anthropic-style adapters may emit a list of content
    blocks where each block may be an object with ``.text`` or a dict
    with a ``"text"`` key. Non-text blocks (tool-use, image) are skipped.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            text_value = block.get("text")
            if isinstance(text_value, str):
                parts.append(text_value)
            continue
        text_value = getattr(block, "text", None)
        if isinstance(text_value, str):
            parts.append(text_value)
    return "".join(parts)
