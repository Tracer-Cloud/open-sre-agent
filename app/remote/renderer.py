"""Terminal renderer for remote agent streaming events.

Reuses spinner and label patterns from app.output so that remote investigation
output looks identical to a local ``opensre investigate`` run.

Handles both ``stream_mode: ["updates"]`` (legacy node-level) and
``stream_mode: ["events"]`` (fine-grained tool/LLM callbacks).
"""

from __future__ import annotations

import math
import re
import sys
import time
from collections.abc import Iterator
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.analytics.source import EntrypointSource
from app.cli.interactive_shell.ui.theme import (
    ANSI_BOLD,
    ANSI_DIM,
    ANSI_RESET,
    BOLD_BRAND_ANSI,
    BRAND,
    HIGHLIGHT_ANSI,
    TEXT_ANSI,
)
from app.output import (
    CtrlOToggleWatcher,
    ProgressTracker,
    get_output_format,
    set_live_console,
    stop_display,
    unregister_live_console,
)
from app.remote.reasoning import reasoning_text
from app.remote.stream import StreamEvent
from app.tools.registry import get_registered_tool_map, resolve_tool_display_name
from app.utils.tool_trace import format_json_preview

_RESET = ANSI_RESET
_DIM = ANSI_DIM
_BOLD = ANSI_BOLD
_WHITE = TEXT_ANSI
_GREEN = HIGHLIGHT_ANSI
_CYAN = BOLD_BRAND_ANSI

_NODE_START_KINDS = frozenset(
    {
        "on_chain_start",
    }
)

_NODE_END_KINDS = frozenset(
    {
        "on_chain_end",
    }
)

# Remote streams emit this kind for every text-token delta from a chat model
# inside a node. Held as a constant alongside the lifecycle kinds above so
# the events-mode handler doesn't carry a magic string.
_TOKEN_STREAM_KIND = "on_chat_model_stream"

# Diagnose is the only node where the LLM's reasoning is visible enough to
# warrant streaming the raw token deltas live as Markdown. Other nodes keep
# the compact spinner UX from ``_LiveSpinner`` in app.output.
_DIAGNOSE_NODE = "diagnose_root_cause"
# Same Rich.Live refresh / spinner choices as the interactive-shell streamer
# so the two surfaces feel identical.
_DIAGNOSE_LIVE_REFRESH = 20
# Same throttle rationale as ``streaming._LIVE_RENDER_INTERVAL_S``: cap
# Markdown(buffer) re-parses to one per refresh window. Without this, the
# diagnose Live region performs O(n²) parsing on long streams and stalls
# visibly past a few thousand tokens.
_DIAGNOSE_RENDER_INTERVAL_S = 1.0 / _DIAGNOSE_LIVE_REFRESH
_DIAGNOSE_SPINNER_NAME = "dots12"
_DIAGNOSE_SPINNER_COLOR = "orange1"
_HIDDEN_PROGRESS_NODES = frozenset({"publish_findings"})


def _render_source(*, local: bool) -> str:
    return EntrypointSource.CLI_PASTE.value if local else EntrypointSource.REMOTE_HTTP.value


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


def _clean_markdown_line(line: str) -> str:
    """Strip both bulleted lists (•, ●, -, —, *) and numbered lists (e.g. 1., 2))."""
    stripped = line.strip()
    prev = ""
    while stripped != prev:
        prev = stripped
        stripped = re.sub(r"^[-•●—]\s+", "", stripped)
        # Markdown ``* item`` list marker only — not ``*Italic Section:*`` headings.
        stripped = re.sub(r"^\*\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
    return stripped


def _normalized_report_heading_inner(line: str) -> str:
    """Normalize LLM report lines for heading keyword matching."""
    s = line.strip()
    while s.startswith("#"):
        s = s[1:].strip()
    if s.startswith("**"):
        core = s[2:]
        if core.endswith("**:"):
            core = core[:-3]
        elif core.endswith("**"):
            core = core[:-2]
        return core.strip()
    if len(s) >= 2 and s.startswith("[") and s.endswith("]") and ":" not in s:
        return s[1:-1].strip()
    if (
        len(s) >= 3
        and s.startswith("*")
        and s.endswith("*")
        and not s.startswith("* ")
        and "**" not in s
    ):
        inner = s[1:-1].strip()
        if ":" in inner or len(inner.split()) >= 3:
            return inner
    return s.strip()


def _report_line_looks_like_heading(line: str, *, inner: str) -> bool:
    """True if the line uses a heading-like structure (not prose)."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    is_bracket = (
        stripped.startswith("[") and stripped.rstrip().endswith("]") and ":" not in stripped
    )
    is_bold_md = stripped.startswith("**") and (stripped.endswith("**") or stripped.endswith("**:"))
    wrapped_ast = (
        len(stripped) >= 3
        and stripped.startswith("*")
        and stripped.endswith("*")
        and not stripped.startswith("* ")
        and "**" not in stripped
        and (":" in stripped[1:-1] or len(stripped[1:-1].strip().split()) >= 3)
    )
    shouty = inner.isupper() and len(inner.replace(" ", "")) >= 8 and len(inner.split()) <= 14
    return bool(is_bracket or is_bold_md or wrapped_ast or shouty)


class StreamRenderer:
    """Renders a stream of remote SSE events as live terminal progress.

    Wraps ProgressTracker to show the same spinners and resolved-dot lines
    that local investigations produce, driven by remote streaming events.
    When receiving ``events``-mode events, the spinner subtext is updated
    in real time with tool calls, LLM reasoning, and other decisions.
    """

    def __init__(self, *, local: bool = False) -> None:
        self._tracker = ProgressTracker()
        self._active_node: str | None = None
        self._events_received: int = 0
        self._node_names_seen: list[str] = []
        self._final_state: dict[str, Any] = {}
        self._stream_completed = False
        self._local = local
        # diagnose_root_cause streams the model's reasoning live as Markdown
        # instead of into the compact spinner subtext. The helper owns the
        # buffer + Live region + throttle state; the renderer only
        # orchestrates lifecycle (active_node tracking, finish-on-end).
        self._console = Console(highlight=False)
        self._diagnose = _DiagnoseStreamRenderer(self._console, self._tracker, local=self._local)
        self._plan_preview_printed = False
        # Track tool call start times keyed by tool name for elapsed display
        self._tool_start_times: dict[str, float] = {}
        self._tool_inputs: dict[str, Any] = {}
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._printed_tool_detail_ids: set[int] = set()
        self._tool_summary_counts: dict[str, dict[str, int]] = {}
        self._tool_summary_order: list[tuple[str, str]] = []
        self._toggle_watcher: CtrlOToggleWatcher | None = None

    def _print_above_renderable(self, renderable: Any) -> None:
        """Print a rich renderable permanently above the active live region (even during diagnose)."""
        if self._diagnose._live is not None and self._diagnose._live.is_started:
            self._diagnose._live.console.print(renderable)
        elif self._tracker.has_active_display:
            self._tracker.print_above_renderable(renderable)
        else:
            self._console.print(renderable)

    @property
    def events_received(self) -> int:
        return self._events_received

    @property
    def node_names_seen(self) -> list[str]:
        return list(self._node_names_seen)

    @property
    def final_state(self) -> dict[str, Any]:
        return dict(self._final_state)

    @property
    def stream_completed(self) -> bool:
        return self._stream_completed

    def _mark_node_seen(self, canonical: str) -> None:
        if canonical not in self._node_names_seen:
            self._node_names_seen.append(canonical)

    def _start_toggle_watcher(self) -> None:
        if get_output_format() != "rich":
            return
        self._toggle_watcher = CtrlOToggleWatcher(self._toggle_tool_details)
        self._toggle_watcher.start()

    def _stop_toggle_watcher(self) -> None:
        if self._toggle_watcher is not None:
            self._toggle_watcher.stop()
            self._toggle_watcher = None

    def _toggle_tool_details(self) -> None:
        self._tool_details_visible = not self._tool_details_visible
        if get_output_format() == "rich" and self._tracker.has_active_display:
            self._sync_tool_detail_view(clear=True)
            return
        label = "shown" if self._tool_details_visible else "hidden"
        self._print_above_renderable(Text(f"  Tool details {label} (ctrl+o)", style="dim"))
        if self._tool_details_visible:
            self._flush_tool_details()

    def _sync_tool_detail_view(self, *, clear: bool = False) -> None:
        if get_output_format() == "rich" and self._tracker.has_active_display:
            self._tracker.set_tool_detail_view(
                visible=self._tool_details_visible,
                records=self._tool_detail_records,
                summary=self._format_tool_summary(),
                clear=clear,
            )

    def render_stream(self, events: Iterator[StreamEvent]) -> dict[str, Any]:
        """Consume a full event stream and render progress to the terminal.

        Returns the accumulated final state dict.
        """
        if not self._local:
            _print_connection_banner()
        self._start_toggle_watcher()

        _interrupted = False
        try:
            for event in events:
                self._handle_event(event)
        except KeyboardInterrupt:
            _interrupted = True
            get_analytics().capture(
                Event.INVESTIGATION_ABANDONED,
                {
                    "stage": self._active_node or "unstarted",
                    "source": _render_source(local=self._local),
                },
            )
            raise
        finally:
            self._stop_toggle_watcher()
            # Always stop the active spinner thread and flush whatever
            # final state was accumulated, even if the stream raises
            # (e.g. LLM quota exhausted). Otherwise the spinner keeps
            # writing \r + erase-line escapes forever, and any partial
            # report the user has been watching stream live would be
            # silently discarded before the exception propagates.
            self._finish_active_node()
            if not _interrupted:
                self._print_report()
        return dict(self._final_state)

    def _handle_event(self, event: StreamEvent) -> None:
        self._events_received += 1

        if event.event_type == "metadata":
            return

        if event.event_type == "end":
            self._stream_completed = True
            self._finish_active_node()
            return

        if event.event_type == "updates":
            self._handle_update(event)
            return

        if event.event_type == "events":
            self._handle_events_mode(event)
            return

    def _handle_update(self, event: StreamEvent) -> None:
        node = event.node_name
        if not node:
            return

        canonical = _canonical_node_name(node)
        if canonical in _HIDDEN_PROGRESS_NODES:
            self._mark_node_seen(canonical)
            self._merge_state(event.data.get(node, event.data))
            return

        if canonical != self._active_node:
            self._finish_active_node()
            self._active_node = canonical
            self._mark_node_seen(canonical)
            self._tracker.start(canonical)

        self._merge_state(event.data.get(node, event.data))

    def _handle_events_mode(self, event: StreamEvent) -> None:
        """Process a fine-grained ``events``-mode SSE event.

        Node lifecycle is inferred from ``on_chain_start`` /
        ``on_chain_end`` events whose pipeline node metadata matches a
        graph-level node.  Sub-node callbacks (tool calls, LLM
        reasoning) update the active spinner's subtext in real time.

        ``diagnose_root_cause`` is special-cased: instead of feeding the
        model's token deltas into a 60-char spinner subtext, the full
        deltas are accumulated into a buffer and rendered live as Markdown
        in a Rich ``Live`` region (matching the interactive-shell handlers).
        """
        node = event.node_name
        kind = event.kind

        if not node:
            return

        canonical = _canonical_node_name(node)

        if canonical in _HIDDEN_PROGRESS_NODES:
            self._mark_node_seen(canonical)
            if kind in _NODE_START_KINDS and self._is_graph_node_event(event):
                self._merge_chain_start_input(event)
                return
            if kind in _NODE_END_KINDS and self._is_graph_node_event(event):
                self._merge_chain_end_output(event)
                return

        if canonical == _DIAGNOSE_NODE:
            if kind in _NODE_START_KINDS and self._is_graph_node_event(event):
                self._merge_chain_start_input(event)
                self._begin_diagnose(canonical)
                return
            if kind in _NODE_END_KINDS and self._is_graph_node_event(event):
                self._merge_chain_end_output(event)
                if self._active_node == canonical:
                    self._end_diagnose()
                return
            if kind == _TOKEN_STREAM_KIND and self._active_node == canonical:
                self._diagnose.append_chunk(event)
                return
            return

        if kind in _NODE_START_KINDS and self._is_graph_node_event(event):
            self._merge_chain_start_input(event)
            if canonical != self._active_node:
                self._finish_active_node()
                self._active_node = canonical
                self._mark_node_seen(canonical)
                self._tracker.start(canonical)
            return

        if kind in _NODE_END_KINDS and self._is_graph_node_event(event):
            self._merge_chain_end_output(event)
            if canonical == self._active_node:
                self._finish_active_node()
            return

        if kind == "on_tool_start":
            self._handle_tool_start(event)
            return

        if kind == "on_tool_end":
            self._handle_tool_end(event)
            return

        if canonical == self._active_node:
            text = reasoning_text(kind, event.data, canonical)
            if text:
                self._tracker.update_subtext(canonical, text)

    def _handle_tool_start(self, event: StreamEvent) -> None:
        data = event.data
        name = data.get("name") or data.get("data", {}).get("name") or "tool"
        event_key = _tool_event_key(data, name)
        self._tool_start_times[event_key] = time.monotonic()
        self._tool_inputs[event_key] = _tool_input(data)
        self._record_tool_summary(name)
        self._update_tool_summary_subtext()

    def _handle_tool_end(self, event: StreamEvent) -> None:
        data = event.data
        name = data.get("name") or data.get("data", {}).get("name") or "tool"
        display = resolve_tool_display_name(name)
        event_key = _tool_event_key(data, name)
        start = self._tool_start_times.pop(event_key, None)
        elapsed = f"  {int((time.monotonic() - start) * 1000)}ms" if start is not None else ""
        self._update_tool_summary_subtext()
        self._record_tool_detail(
            display,
            self._tool_inputs.pop(event_key, None),
            _tool_output(data),
            elapsed=elapsed.strip(),
        )

    def _record_tool_summary(self, tool_name: str) -> None:
        source = _tool_source_label(tool_name)
        label = _tool_short_label(tool_name, source)
        source_counts = self._tool_summary_counts.setdefault(source, {})
        if label not in source_counts:
            self._tool_summary_order.append((source, label))
        source_counts[label] = source_counts.get(label, 0) + 1
        self._sync_tool_detail_view()

    def _update_tool_summary_subtext(self) -> None:
        if not self._active_node:
            return
        summary = self._format_tool_summary()
        if summary:
            self._tracker.update_subtext(self._active_node, summary, duration=30.0)

    def _format_tool_summary(self) -> str:
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
            if get_output_format() == "rich" and self._tracker.has_active_display:
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
        if get_output_format() == "rich":
            detail = Text()
            detail.append(f"  Tool details: {display}{suffix}\n", style="bold")
            for line in body.splitlines():
                detail.append(f"    {line}\n", style="dim")
            self._print_above_renderable(detail)
            self._printed_tool_detail_ids.add(id(record))
            return
        self._console.print(f"  Tool details: {display}{suffix}", markup=False)
        for line in body.splitlines():
            self._console.print(f"      {line}", markup=False)
        self._printed_tool_detail_ids.add(id(record))

    def _begin_diagnose(self, canonical: str) -> None:
        """Mark diagnose as the active node and let the helper open its Live region.

        Closes any previous spinner-driven node (e.g. ``investigate``)
        first so the helper takes over stdout cleanly.
        """
        if self._active_node and self._active_node != canonical:
            self._finish_active_node()
        self._active_node = canonical
        self._mark_node_seen(canonical)
        self._diagnose.start()

    def _end_diagnose(self) -> None:
        """Close the diagnose helper's Live region and clear ``_active_node``."""
        self._diagnose.finish(self._build_node_message(_DIAGNOSE_NODE))
        self._active_node = None

    @staticmethod
    def _is_graph_node_event(event: StreamEvent) -> bool:
        """True when the event is a top-level graph node transition.

        Top-level graph node chains are tagged with ``graph:step:<N>``.
        Sub-chains inside a node (tool executors, LLM calls) lack this tag.
        """
        name = str(event.data.get("name", ""))
        tags = event.tags
        if any(t.startswith("graph:step:") for t in tags):
            return True
        if any(t.startswith("tracing:") for t in tags):
            return False
        return bool(name == event.node_name)

    def _finish_active_node(self) -> None:
        if self._active_node is None:
            return
        # Diagnose owns its own Rich.Live region — route cleanup through
        # _end_diagnose so the Live closes even on mid-stream exceptions.
        if self._active_node == _DIAGNOSE_NODE:
            self._end_diagnose()
            return
        node = self._active_node
        message = self._build_node_message(node)
        self._tracker.complete(node, message=message)
        if (
            node == "plan_actions"
            and get_output_format() == "rich"
            and not self._plan_preview_printed
        ):
            actions = self._final_state.get("planned_actions", [])
            if actions:
                panel = Panel(
                    "\n".join(
                        f"  [bold green]{i + 1}.[/bold green] [white]{escape(resolve_tool_display_name(act))}[/white]"
                        for i, act in enumerate(actions)
                    ),
                    title="[bold yellow]📋 Investigation Plan Preview[/bold yellow]",
                    border_style="yellow",
                    expand=False,
                )
                self._print_above_renderable(panel)
                self._plan_preview_printed = True
        self._active_node = None

    def _merge_state(self, update: Any) -> None:
        if isinstance(update, dict):
            self._final_state.update(update)

    def _merge_chain_start_input(self, event: StreamEvent) -> None:
        """Pull the ``input`` payload from a chain-start event into ``_final_state``."""
        data = event.data.get("data", {})
        input_payload = data.get("input", {})
        if isinstance(input_payload, dict):
            self._merge_state(input_payload)

    def _merge_chain_end_output(self, event: StreamEvent) -> None:
        """Pull the ``output`` payload from a chain-end event into ``_final_state``.

        Both the diagnose-streaming branch and the default-spinner branch
        unwrap ``event.data["data"]["output"]`` the same way; sharing one
        helper keeps the unwrapping shape in one place.
        """
        output = event.data.get("data", {}).get("output", {})
        if isinstance(output, dict):
            self._merge_state(output)

    def _build_node_message(self, node: str) -> str | None:
        if node == "plan_actions":
            actions = self._final_state.get("planned_actions", [])
            if actions:
                if get_output_format() == "rich":
                    return None
                return f"Planned actions: {actions}"
        if node == "resolve_integrations":
            integrations = self._final_state.get("resolved_integrations", {})
            if integrations:
                names = list(integrations.keys())
                return f"Resolved: {names}"
        if node in {"diagnose", "diagnose_root_cause"}:
            pct = _validity_score_percent(self._final_state.get("validity_score"))
            if pct:
                return f"validity:{pct}"
        return None

    def _print_report(self) -> None:
        from app.output import stop_display

        stop_display()

        slack_message = self._final_state.get("slack_message") or self._final_state.get(
            "report", ""
        )
        root_cause_category = self._final_state.get("root_cause_category")

        if not slack_message:
            if self._final_state.get("is_noise"):
                _print_info("Alert classified as noise — no investigation needed.")
            elif self._events_received == 0:
                _print_info("No events received from the remote agent.")
            return

        from app.delivery.publish_findings.renderers.terminal import render_report as _render

        _render(slack_message, root_cause_category=root_cause_category)


def _canonical_node_name(name: str) -> str:
    """Map node names to the canonical names used by ProgressTracker."""
    mapping = {
        "diagnose_root_cause": "diagnose_root_cause",
        "diagnose": "diagnose_root_cause",
        "publish_findings": "publish_findings",
        "publish": "publish_findings",
        "investigation_agent": "investigation_agent",
    }
    return mapping.get(name, name)


def _tool_event_key(data: dict[str, Any], name: str) -> str:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return str(
        data.get("id")
        or data.get("tool_call_id")
        or nested.get("id")
        or nested.get("tool_call_id")
        or name
    )


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


def _tool_input(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("input", nested.get("input", {}))


def _tool_output(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("output", nested.get("output", {}))


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


def _print_connection_banner() -> None:
    if get_output_format() == "rich":
        sys.stdout.write(
            f"\n  {_BOLD}{_CYAN}Remote Investigation{_RESET}"
            f"  {_DIM}streaming from deployed agent{_RESET}\n\n"
        )
    else:
        print("\n  Remote Investigation  streaming from deployed agent\n")
    sys.stdout.flush()


def _print_section(title: str, content: str, console: Any | None = None) -> None:
    if get_output_format() == "rich":
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding
        from rich.rule import Rule

        from app.cli.interactive_shell.ui.theme import MARKDOWN_THEME

        c = console or Console(highlight=False)
        c.print()
        c.print(Rule(f"[bold] {title} [/]", style=BRAND, align="left"))
        with c.use_theme(MARKDOWN_THEME):
            c.print(Padding(Markdown(content.strip(), code_theme="ansi_dark"), (1, 2)))
    else:
        print(f"\n  {title}")
        for line in content.strip().splitlines():
            print(f"  {line}")
    sys.stdout.flush()


def _print_info(message: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_DIM}{message}{_RESET}\n")
    else:
        print(f"\n  {message}")
    sys.stdout.flush()


def _validity_score_percent(score: Any) -> str | None:
    """Format a 0..1 validity score for display, or None if the payload is unusable."""
    if score is None or isinstance(score, bool):
        return None
    if not isinstance(score, (int, float)):
        return None
    v = float(score)
    if not math.isfinite(v):
        return None
    v = max(0.0, min(1.0, v))
    return f"{int(v * 100)}%"
