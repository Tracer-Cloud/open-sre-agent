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
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from app.output import (
    ProgressTracker,
    get_output_format,
    render_investigation_header,
)
from app.remote.reasoning import reasoning_text
from app.remote.stream import StreamEvent
from app.tools.registry import resolve_tool_display_name

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
        self._console = Console(highlight=False)
        self._alert_header_printed = False
        self._plan_preview_printed = False

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
        self._print_alert_header()

        if get_output_format() != "rich":
            sys.stdout.write(f"  … {canonical}\n")
            sys.stdout.flush()
            return

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
            console=self._console,
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
            self._active_node = None
            return
        node = self._active_node
        message = self._build_node_message(node)
        self._tracker.complete(node, message=message)
        if get_output_format() == "rich":
            self._print_alert_header()
        if (
            node == "plan_actions"
            and get_output_format() == "rich"
            and not self._plan_preview_printed
        ):
            actions = self._final_state.get("planned_actions", [])
            if actions:
                self._tracker.stop()
                self._console.print()
                self._console.print(
                    Panel(
                        "\n".join(
                            f"  [bold green]{i + 1}.[/bold green] [white]{escape(resolve_tool_display_name(act))}[/white]"
                            for i, act in enumerate(actions)
                        ),
                        title="[bold yellow]📋 Investigation Plan Preview[/bold yellow]",
                        border_style="yellow",
                        expand=False,
                    )
                )
                self._console.print()
                self._plan_preview_printed = True
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
                if get_output_format() == "rich":
                    return None
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

    def _print_alert_header(self) -> None:
        if self._alert_header_printed:
            return
        alert_name = self._final_state.get("alert_name", "Unknown")
        pipeline = self._final_state.get("pipeline_name", "Unknown")
        severity = self._final_state.get("severity", "unknown")

        if alert_name != "Unknown" or pipeline != "Unknown":
            if get_output_format() == "rich":
                self._tracker.stop()
                self._console.print()
                self._console.print(
                    Panel(
                        f"  • [dim]Source/Name:[/dim] [bold white]{escape(alert_name)}[/bold white]\n"
                        f"  • [dim]Pipeline:[/dim] [cyan]{escape(pipeline)}[/cyan]\n"
                        f"  • [dim]Severity:[/dim] [bold yellow]{escape(severity)}[/bold yellow]",
                        title="[bold cyan]📥 Alert Ingested & Parsed[/bold cyan]",
                        border_style="cyan",
                        expand=False,
                    )
                )
            else:
                render_investigation_header(alert_name, pipeline, severity)
            self._alert_header_printed = True

    def _print_report(self) -> None:
        from app.output import stop_display

        stop_display()

        self._print_alert_header()

        root_cause = self._final_state.get("root_cause", "")
        report = self._final_state.get("report", "")
        score = self._final_state.get("validity_score")
        confidence_str = f"{int(score * 100)}%" if score is not None else "N/A"

        if get_output_format() == "rich" and root_cause:
            self._console.print()

            evidence_lines = []
            next_actions = []

            lines = report.strip().splitlines()
            current_section = None
            consumed_indices = set()

            def is_header_candidate(line: str, keywords: list[str]) -> bool:
                stripped = line.strip()
                if not stripped:
                    return False
                stripped_clean = stripped.lstrip("#").strip()
                if len(stripped_clean) > 60:
                    return False
                if stripped_clean[-1] in {".", "?", "!"}:
                    return False
                lowered = stripped_clean.lower()
                return any(kw in lowered for kw in keywords)

            for idx, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue

                if is_header_candidate(line, ["supporting evidence"]):
                    current_section = "evidence"
                    consumed_indices.add(idx)
                    continue
                elif is_header_candidate(
                    line, ["next actions", "next steps", "remediation", "recommendations"]
                ):
                    current_section = "next_actions"
                    consumed_indices.add(idx)
                    continue
                elif is_header_candidate(line, ["root cause"]):
                    current_section = "root_cause"
                    consumed_indices.add(idx)
                    continue

                if current_section in ("evidence", "next_actions"):
                    if is_header_candidate(
                        line,
                        [
                            "supporting evidence",
                            "next actions",
                            "next steps",
                            "remediation",
                            "recommendations",
                            "root cause",
                        ],
                    ):
                        if is_header_candidate(line, ["supporting evidence"]):
                            current_section = "evidence"
                        elif is_header_candidate(
                            line, ["next actions", "next steps", "remediation", "recommendations"]
                        ):
                            current_section = "next_actions"
                        else:
                            current_section = "root_cause"
                        consumed_indices.add(idx)
                        continue

                    clean_line = stripped.lstrip("•●-—* ").strip()
                    if clean_line:
                        consumed_indices.add(idx)
                        if current_section == "evidence":
                            evidence_lines.append(clean_line)
                        else:
                            next_actions.append(clean_line)

            if not next_actions:
                action_verbs = {
                    "check",
                    "review",
                    "restart",
                    "fix",
                    "verify",
                    "investigate",
                    "debug",
                    "update",
                    "scale",
                    "run",
                    "test",
                }
                for idx, line in enumerate(lines):
                    if idx in consumed_indices:
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    clean_line = stripped.lstrip("•●-—* ").strip()
                    tokens = clean_line.lower().split()
                    if tokens and tokens[0] in action_verbs:
                        next_actions.append(clean_line)

            content = f"[bold white][Root Cause][/bold white]\n  {escape(root_cause)}\n\n"
            content += f"[bold white][Confidence][/bold white]\n  [bold green]{escape(confidence_str)}[/bold green]\n\n"

            if evidence_lines:
                content += "[bold white][Supporting Evidence][/bold white]\n"
                for ev in evidence_lines:
                    content += f"  • {escape(ev)}\n"
                content += "\n"

            if next_actions:
                content += "[bold white][Next Actions][/bold white]\n"
                for act in next_actions:
                    content += f"  • {escape(act)}\n"

            self._console.print(
                Panel(
                    content.strip(),
                    title="[bold green]🏆 Final Root Cause Analysis (RCA)[/bold green]",
                    border_style="green",
                    expand=False,
                )
            )
            self._console.print()
        else:
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
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding
        from rich.rule import Rule

        console = Console(highlight=False)
        console.print()
        console.print(Rule(f"[bold white] {title} [/]", style="#2D4A2D", align="left"))
        console.print(Padding(Markdown(content.strip()), (1, 2)))
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
