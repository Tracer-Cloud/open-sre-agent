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

from app.cli.interactive_shell.theme import (
    ANSI_BOLD,
    ANSI_DIM,
    ANSI_RESET,
    BOLD_BRAND_ANSI,
    BRAND,
    HIGHLIGHT_ANSI,
    TEXT_ANSI,
)
from app.output import (
    ProgressTracker,
    get_output_format,
    render_investigation_header,
    stop_display,
)
from app.remote.reasoning import reasoning_text
from app.remote.stream import StreamEvent
from app.tools.registry import resolve_tool_display_name

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
# Same throttle rationale as ``streaming._LIVE_RENDER_INTERVAL_S``: cap
# Markdown(buffer) re-parses to one per refresh window. Without this, the
# diagnose Live region performs O(n²) parsing on long streams and stalls
# visibly past a few thousand tokens.
_DIAGNOSE_RENDER_INTERVAL_S = 1.0 / _DIAGNOSE_LIVE_REFRESH
_DIAGNOSE_SPINNER_NAME = "dots12"
_DIAGNOSE_SPINNER_COLOR = "orange1"


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

    def __init__(self, console: Console | None = None) -> None:
        self.buffer: list[str] = []
        self._live: Live | None = None
        self._started: float = 0.0
        # Last time we re-rendered ``Markdown(buffer)`` into the Live region.
        # Throttled to ``_DIAGNOSE_RENDER_INTERVAL_S`` so long streams don't
        # incur O(n²) parsing.
        self._last_render: float = 0.0
        self._console: Console | None = console

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

        # Stop any active ProgressTracker Live display before opening our own
        # Live region. Two competing Rich Live regions cause visible flicker;
        # keeping only one Live at a time eliminates it. The next graph node
        # (publish_findings) already stops the display unconditionally, so
        # this just moves the stop point one node earlier.
        stop_display()

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
        self._live.start()

    def append_chunk(self, event: StreamEvent) -> None:
        """Append a token delta to the buffer; refresh the Live region (throttled).

        The chunk's ``content`` shape varies by provider: OpenAI emits a
        plain string; langchain-anthropic emits a list of content blocks.
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
        # instead of into the compact spinner subtext. The helper owns the
        # buffer + Live region + throttle state; the renderer only
        # orchestrates lifecycle (active_node tracking, finish-on-end).
        self._console = Console(highlight=False)
        self._diagnose = _DiagnoseStreamRenderer(self._console)
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

    def _begin_diagnose(self, canonical: str) -> None:
        """Mark diagnose as the active node and let the helper open its Live region.

        Closes any previous spinner-driven node (e.g. ``investigate``)
        first so the helper takes over stdout cleanly.
        """
        if self._active_node and self._active_node != canonical:
            self._finish_active_node()
        self._active_node = canonical
        if canonical not in self._node_names_seen:
            self._node_names_seen.append(canonical)
        self._print_alert_header()
        self._diagnose.start()

    def _end_diagnose(self) -> None:
        """Close the diagnose helper's Live region and clear ``_active_node``."""
        self._diagnose.finish(self._build_node_message(_DIAGNOSE_NODE))
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
        # Diagnose owns its own Rich.Live region — route cleanup through
        # _end_diagnose so the Live closes even on mid-stream exceptions.
        if self._active_node == _DIAGNOSE_NODE:
            self._end_diagnose()
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
                panel = Panel(
                    "\n".join(
                        f"  [bold green]{i + 1}.[/bold green] [white]{escape(resolve_tool_display_name(act))}[/white]"
                        for i, act in enumerate(actions)
                    ),
                    title="[bold yellow]📋 Investigation Plan Preview[/bold yellow]",
                    border_style="yellow",
                    expand=False,
                )
                if self._tracker._display:
                    self._tracker._display._live.console.print(panel)
                else:
                    self._console.print()
                    self._console.print(panel)
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
                panel = Panel(
                    f"  • [dim]Source/Name:[/dim] [bold white]{escape(alert_name)}[/bold white]\n"
                    f"  • [dim]Pipeline:[/dim] [cyan]{escape(pipeline)}[/cyan]\n"
                    f"  • [dim]Severity:[/dim] [bold yellow]{escape(severity)}[/bold yellow]",
                    title="[bold cyan]📥 Alert Ingested & Parsed[/bold cyan]",
                    border_style="cyan",
                    expand=False,
                )
                if self._tracker._display:
                    self._tracker._display._live.console.print(panel)
                else:
                    self._console.print()
                    self._console.print(panel)
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
                if not stripped_clean:
                    return False
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
            # Skip the Root Cause one-liner if the diagnose node already streamed
            # its reasoning live — the user has just watched the full analysis
            # appear on screen, so the condensed summary adds noise rather than
            # value. The Report section still prints because publish_findings
            # adds alert framing and timing the diagnose stream doesn't carry.
            diagnose_streamed = self._diagnose.streamed
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

        from app.cli.interactive_shell.theme import MARKDOWN_THEME

        console = Console(highlight=False)
        console.print()
        console.print(Rule(f"[bold] {title} [/]", style=BRAND, align="left"))
        with console.use_theme(MARKDOWN_THEME):
            console.print(Padding(Markdown(content.strip(), code_theme="ansi_dark"), (1, 2)))
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
