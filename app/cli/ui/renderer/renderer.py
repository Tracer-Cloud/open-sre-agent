"""Terminal renderer for streamed investigation events."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from rich.console import Console
from rich.text import Text

from app.analytics.events import Event
from app.analytics.provider import get_analytics
from app.cli.interactive_shell.ui.output import (
    ProgressTracker,
    _fmt_timing,
    _repl_progress_active,
    get_output_format,
    register_tool_detail_toggle,
)
from app.cli.ui.renderer.constants import (
    _DIAGNOSE_NODE,
    _HIDDEN_PROGRESS_NODES,
    _NODE_END_KINDS,
    _NODE_START_KINDS,
    _TOKEN_STREAM_KIND,
    _render_source,
)
from app.cli.ui.renderer.diagnose import _DiagnoseStreamRenderer
from app.cli.ui.renderer.formatting import _validity_score_percent, investigation_llm_progress_hint
from app.cli.ui.renderer.terminal import _print_connection_banner, _print_info
from app.cli.ui.renderer.tools import (
    _tool_event_key,
    _tool_input,
    _tool_output,
    _tool_short_label,
    _tool_source_label,
)
from app.remote.reasoning import reasoning_text
from app.remote.stream import StreamEvent
from app.tools.registry import resolve_tool_display_name
from app.utils.tool_trace import format_json_preview


class StreamRenderer:
    """Renders a stream of remote SSE events as live terminal progress.

    Wraps ProgressTracker to show the same spinners and resolved-dot lines
    that local investigations produce, driven by remote streaming events.
    When receiving ``events``-mode events, the spinner subtext is updated
    in real time with tool calls, LLM reasoning, and other decisions.
    """

    def __init__(self, *, local: bool = False, display: bool = True) -> None:
        self._tracker = ProgressTracker()
        self._active_node: str | None = None
        self._events_received: int = 0
        self._node_names_seen: list[str] = []
        self._final_state: dict[str, Any] = {}
        self._stream_completed = False
        self._local = local
        self._display = display
        # diagnose_root_cause streams the model's reasoning live as Markdown
        # instead of into the compact spinner subtext. The helper owns the
        # buffer + Live region + throttle state; the renderer only
        # orchestrates lifecycle (active_node tracking, finish-on-end).
        self._console = Console(highlight=False)
        self._diagnose = _DiagnoseStreamRenderer(self._console, self._tracker, local=self._local)
        # Track tool call start times keyed by tool name for elapsed display
        self._tool_start_times: dict[str, float] = {}
        self._tool_inputs: dict[str, Any] = {}
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._printed_tool_detail_ids: set[int] = set()
        self._tool_summary_counts: dict[str, dict[str, int]] = {}
        self._tool_summary_order: list[tuple[str, str]] = []
        self._prior_lap_tools: list[str] = []
        self._toggle_unregister: Callable[[], None] | None = None

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
        if get_output_format() != "rich" or _repl_progress_active():
            return
        # ProgressTracker already owns the single Ctrl+O stdin watcher.
        # Register our handler so toggle_active_tool_details() routes here.
        self._toggle_unregister = register_tool_detail_toggle(self._toggle_tool_details)

    def _stop_toggle_watcher(self) -> None:
        if self._toggle_unregister is not None:
            self._toggle_unregister()
            self._toggle_unregister = None

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
            set_tool_detail_view = getattr(self._tracker, "set_tool_detail_view", None)
            if callable(set_tool_detail_view):
                set_tool_detail_view(
                    visible=self._tool_details_visible,
                    records=self._tool_detail_records,
                    summary=self._format_tool_summary(),
                    clear=clear,
                )
                return
            display = getattr(self._tracker, "_display", None)
            set_tool_details = getattr(display, "set_tool_details", None)
            if callable(set_tool_details):
                set_tool_details(
                    visible=self._tool_details_visible,
                    records=self._tool_detail_records,
                    summary=self._format_tool_summary(),
                    clear=clear,
                )

    def render_stream(self, events: Iterator[StreamEvent]) -> dict[str, Any]:
        """Consume a full event stream and render progress to the terminal.

        Returns the accumulated final state dict.
        """
        if not self._local and self._display:
            _print_connection_banner()
        if self._display:
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
            self._tracker.stop()
            if self._display and not _interrupted:
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
                if canonical == "investigation_agent":
                    self._prior_lap_tools = []
                    # Prime the Live spinner subtext; hint line printed on first llm_start.
                    primed = investigation_llm_progress_hint(0)
                    self._tracker.update_subtext(canonical, f"{primed} ·", duration=300.0)
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

        if kind == "on_llm_start":
            self._handle_llm_start(event)
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
        # Show "calling X..." briefly in spinner; aggregate summary shown on end.
        if self._active_node:
            current = resolve_tool_display_name(name)
            self._tracker.update_subtext(self._active_node, f"calling {current}...", duration=15.0)

    def _handle_tool_end(self, event: StreamEvent) -> None:
        data = event.data
        name = data.get("name") or data.get("data", {}).get("name") or "tool"
        display = resolve_tool_display_name(name)
        event_key = _tool_event_key(data, name)
        start = self._tool_start_times.pop(event_key, None)
        elapsed_ms = int((time.monotonic() - start) * 1000) if start is not None else None
        elapsed_str = _fmt_timing(elapsed_ms) if elapsed_ms is not None else ""
        self._update_tool_summary_subtext()
        self._record_tool_detail(
            display,
            self._tool_inputs.pop(event_key, None),
            _tool_output(data),
            elapsed=elapsed_str,
        )
        if elapsed_ms is not None:
            print_tool_call_line = getattr(self._tracker, "print_tool_call_line", None)
            if callable(print_tool_call_line):
                print_tool_call_line(name, elapsed_ms)
        if self._active_node == "investigation_agent":
            self._prior_lap_tools.append(display)

    def _handle_llm_start(self, event: StreamEvent) -> None:
        if self._active_node != "investigation_agent":
            return
        iteration = event.data.get("iteration", 0)
        if not isinstance(iteration, int):
            iteration = 0
        base = investigation_llm_progress_hint(
            iteration,
            prior_tools=self._prior_lap_tools,
        )
        self._prior_lap_tools = []
        dots = "·" * ((iteration % 3) + 1)
        hint = f"{base} {dots}"
        self._tracker.update_subtext(self._active_node, hint, duration=300.0)
        self._tracker.print_status_hint(hint)

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
        from app.cli.interactive_shell.ui.output import stop_display

        stop_display()

        slack_message = self._final_state.get("slack_message") or self._final_state.get(
            "report", ""
        )

        if not slack_message:
            if self._final_state.get("is_noise"):
                _print_info("Alert classified as noise — no investigation needed.")
            elif self._events_received == 0:
                _print_info("No events received from the remote agent.")
            return

        from app.core.orchestration.node.publish_findings.renderers.terminal import (
            render_report as _render,
        )

        _render(slack_message)


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
