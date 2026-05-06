"""Terminal renderer for remote agent streaming events.

Reuses spinner and label patterns from app.output so that remote investigation
output looks identical to a local ``opensre investigate`` run.

Handles both ``stream_mode: ["updates"]`` (legacy node-level) and
``stream_mode: ["events"]`` (fine-grained tool/LLM callbacks).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from app.output import (
    ProgressTracker,
    get_output_format,
    render_investigation_header,
)
from app.remote.reasoning import reasoning_text
from app.remote.stream import StreamEvent

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_WHITE = "\033[37m"
_GREEN = "\033[32m"
_CYAN = "\033[1;36m"

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

# LangGraph emits this kind for every text-token delta from a chat model
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
_DIAGNOSE_SPINNER_NAME = "dots12"
_DIAGNOSE_SPINNER_COLOR = "orange1"


class StreamRenderer:
    """Renders a stream of LangGraph SSE events as live terminal progress.

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
        # instead of into the compact spinner subtext. Buffer always
        # accumulates; ``_diagnose_live`` only opens in rich-output mode.
        self._diagnose_buffer: list[str] = []
        self._diagnose_live: Live | None = None
        self._diagnose_started: float = 0.0
        # Lazy-init: only constructed when the diagnose node first runs.
        self._diagnose_console: Console | None = None

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

    def render_stream(self, events: Iterator[StreamEvent]) -> dict[str, Any]:
        """Consume a full event stream and render progress to the terminal.

        Returns the accumulated final state dict.
        """
        if not self._local:
            _print_connection_banner()

        try:
            for event in events:
                self._handle_event(event)
        finally:
            # Always stop the active spinner thread and flush whatever
            # final state was accumulated, even if the stream raises
            # (e.g. LLM quota exhausted). Otherwise the spinner keeps
            # writing \r + erase-line escapes forever, and any partial
            # report the user has been watching stream live would be
            # silently discarded before the exception propagates.
            self._finish_active_node()
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

        if canonical != self._active_node:
            self._finish_active_node()
            self._active_node = canonical
            if canonical not in self._node_names_seen:
                self._node_names_seen.append(canonical)
            self._tracker.start(canonical)

        self._merge_state(event.data.get(node, event.data))

    def _handle_events_mode(self, event: StreamEvent) -> None:
        """Process a fine-grained ``events``-mode SSE event.

        Node lifecycle is inferred from ``on_chain_start`` /
        ``on_chain_end`` events whose ``langgraph_node`` matches a
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

        if canonical == _DIAGNOSE_NODE:
            if kind in _NODE_START_KINDS and self._is_graph_node_event(event):
                self._start_diagnose_streaming(canonical)
                return
            if kind in _NODE_END_KINDS and self._is_graph_node_event(event):
                self._merge_chain_end_output(event)
                if self._active_node == canonical:
                    self._finish_diagnose_streaming()
                return
            if kind == _TOKEN_STREAM_KIND and self._active_node == canonical:
                self._append_diagnose_chunk(event)
                return
            # Other diagnose-related callbacks (tool starts, sub-chain
            # events, etc.) intentionally don't fall through to the
            # spinner-subtext path — diagnose owns its own Rich.Live region.
            return

        if kind in _NODE_START_KINDS and self._is_graph_node_event(event):
            if canonical != self._active_node:
                self._finish_active_node()
                self._active_node = canonical
                if canonical not in self._node_names_seen:
                    self._node_names_seen.append(canonical)
                self._tracker.start(canonical)
            return

        if kind in _NODE_END_KINDS and self._is_graph_node_event(event):
            self._merge_chain_end_output(event)
            if canonical == self._active_node:
                self._finish_active_node()
            return

        if canonical == self._active_node:
            text = reasoning_text(kind, event.data, canonical)
            if text:
                self._tracker.update_subtext(canonical, text)

    def _start_diagnose_streaming(self, canonical: str) -> None:
        """Begin the diagnose-streaming branch.

        Closes any previous spinner-driven node (e.g. ``investigate``)
        before taking over stdout for the Live region.
        """
        if self._active_node and self._active_node != canonical:
            self._finish_active_node()
        self._active_node = canonical
        if canonical not in self._node_names_seen:
            self._node_names_seen.append(canonical)
        self._diagnose_buffer = []
        self._diagnose_started = time.monotonic()

        if get_output_format() != "rich":
            sys.stdout.write(f"  … {canonical}\n")
            sys.stdout.flush()
            return

        if self._diagnose_console is None:
            self._diagnose_console = Console(highlight=False)
        spinner = Spinner(
            _DIAGNOSE_SPINNER_NAME,
            text=Text(
                f"{canonical}  reasoning…",
                style=f"bold {_DIAGNOSE_SPINNER_COLOR}",
            ),
            style=f"bold {_DIAGNOSE_SPINNER_COLOR}",
        )
        self._diagnose_live = Live(
            spinner,
            console=self._diagnose_console,
            refresh_per_second=_DIAGNOSE_LIVE_REFRESH,
            transient=False,
        )
        self._diagnose_live.start()

    def _append_diagnose_chunk(self, event: StreamEvent) -> None:
        """Append a token delta to the diagnose buffer; refresh the Live region.

        The chunk's ``content`` shape varies by provider: OpenAI emits a plain
        string; langchain-anthropic emits a list of content blocks (objects
        with ``.text`` or dicts with a ``"text"`` key). Flatten the list shape
        to the same text the OpenAI path produces — calling ``str()`` on a
        block list would render its Python repr instead of the reasoning.
        """
        chunk = event.data.get("data", {}).get("chunk", {})
        content = chunk.get("content", "") if isinstance(chunk, dict) else ""
        if not content:
            return
        text = _flatten_chunk_content(content)
        if not text:
            return
        self._diagnose_buffer.append(text)
        if self._diagnose_live is not None:
            self._diagnose_live.update(Markdown("".join(self._diagnose_buffer)))

    def _finish_diagnose_streaming(self) -> None:
        """Close the diagnose Live region and print the resolved-dot line.

        Also handles the text-mode fallback: replays the accumulated buffer
        as plain lines (since text mode never opened a Live region).
        """
        elapsed = time.monotonic() - self._diagnose_started
        message = self._build_node_message(_DIAGNOSE_NODE)

        if self._diagnose_live is not None:
            try:
                self._diagnose_live.stop()
            finally:
                self._diagnose_live = None
            sys.stdout.write(
                f"  {_GREEN}●{_RESET}  {_BOLD}{_WHITE}{_DIAGNOSE_NODE}{_RESET}"
                f"  {_DIM}{elapsed:.1f}s{_RESET}"
            )
            if message:
                sys.stdout.write(f"  {_DIM}{message}{_RESET}")
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            if self._diagnose_buffer:
                for line in "".join(self._diagnose_buffer).strip().splitlines():
                    print(f"  {line}")
            tail = f"  ● {_DIAGNOSE_NODE}  {elapsed:.1f}s"
            if message:
                tail += f"  {message}"
            print(tail)

        self._active_node = None

    @staticmethod
    def _is_graph_node_event(event: StreamEvent) -> bool:
        """True when the event is a top-level graph node transition.

        LangGraph tags graph-level node chains with ``graph:step:<N>``.
        Sub-chains inside a node (tool executors, LLM calls) lack this tag.
        """
        name = str(event.data.get("name", ""))
        tags = event.tags
        if any(t.startswith("graph:step:") for t in tags):
            return True
        if any(t.startswith("langsmith:") for t in tags):
            return False
        return bool(name == event.node_name)

    def _finish_active_node(self) -> None:
        if self._active_node is None:
            return
        # Diagnose is streamed via Rich.Live, not the spinner tracker — route
        # cleanup through the streaming finish so the Live region is always
        # closed even on mid-stream exceptions.
        if self._active_node == _DIAGNOSE_NODE:
            self._finish_diagnose_streaming()
            return
        message = self._build_node_message(self._active_node)
        self._tracker.complete(self._active_node, message=message)
        self._active_node = None

    def _merge_state(self, update: Any) -> None:
        if isinstance(update, dict):
            self._final_state.update(update)

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
                return f"Planned actions: {actions}"
        if node == "resolve_integrations":
            integrations = self._final_state.get("resolved_integrations", {})
            if integrations:
                names = list(integrations.keys())
                return f"Resolved: {names}"
        if node in {"diagnose", "diagnose_root_cause"}:
            score = self._final_state.get("validity_score")
            if score is not None:
                return f"validity:{int(score * 100)}%"
        return None

    def _print_report(self) -> None:
        alert_name = self._final_state.get("alert_name", "Unknown")
        pipeline = self._final_state.get("pipeline_name", "Unknown")
        severity = self._final_state.get("severity", "unknown")

        if alert_name != "Unknown" or pipeline != "Unknown":
            render_investigation_header(alert_name, pipeline, severity)

        root_cause = self._final_state.get("root_cause", "")
        report = self._final_state.get("report", "")

        # Skip the Root Cause one-liner if the diagnose node already streamed
        # its reasoning live — the user has just watched the full analysis
        # appear on screen, so the condensed summary adds noise rather than
        # value. The Report section still prints because publish_findings
        # adds alert framing and timing the diagnose stream doesn't carry.
        diagnose_streamed = bool(self._diagnose_buffer)
        if root_cause and not diagnose_streamed:
            _print_section("Root Cause", root_cause)
        if report:
            _print_section("Report", report)
        elif not root_cause:
            if self._final_state.get("is_noise"):
                _print_info("Alert classified as noise — no investigation needed.")
            elif self._events_received == 0:
                _print_info("No events received from the remote agent.")


def _canonical_node_name(name: str) -> str:
    """Map LangGraph node names to the canonical names used by ProgressTracker."""
    mapping = {
        "diagnose_root_cause": "diagnose_root_cause",
        "diagnose": "diagnose_root_cause",
        "publish_findings": "publish_findings",
        "publish": "publish_findings",
    }
    return mapping.get(name, name)


def _flatten_chunk_content(content: Any) -> str:
    """Resolve a chat-model chunk's ``content`` to plain text.

    OpenAI emits a string. langchain-anthropic emits a list of content
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


def _print_section(title: str, content: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_BOLD}{_WHITE}{title}{_RESET}\n")
        for line in content.strip().splitlines():
            sys.stdout.write(f"  {_DIM}{line}{_RESET}\n")
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
