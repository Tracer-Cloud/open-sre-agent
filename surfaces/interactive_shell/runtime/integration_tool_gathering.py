"""The shell's gather ports: console progress lines and session persistence.

The bounded think -> call-tools -> observe loop is the harness's
:func:`core.agent_harness.turns.evidence_driver.gather_tool_evidence`, driven by
:class:`HeadlessAgent`. This module supplies the two ports a REPL plugs into it
as a :class:`~core.agent_harness.turns.gather_phase.GatherPhase`: a
console-bound progress renderer and a persister into the shell's session store.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from rich.console import Console
from rich.markup import escape

from core.agent_harness.runtime import GatherPhase
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM
from tools.registry import resolve_tool_activity_labels

# Cap so a chatty tool result can't blow up persistence writes.
_MAX_PER_TOOL_CHARS = 4_000

# Keys most likely to distinguish back-to-back calls to the same tool.
_GATHER_INPUT_HINT_KEYS: tuple[str, ...] = (
    "metric_name",
    "query",
    "search",
    "filter",
    "expression",
    "promql",
    "service_name",
    "owner",
    "repo",
    "log_group",
    "monitor_id",
    "alert_id",
    "issue_id",
    "trace_id",
    "span_id",
    "dashboard_uid",
    "panel_id",
    "from",
    "to",
    "time_range",
)


def _truncate_hint(text: str, *, max_len: int = 48) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 1]}…"


def _tool_input_hint(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    hints: list[str] = []
    seen: set[str] = set()
    for key in _GATHER_INPUT_HINT_KEYS:
        value = tool_input.get(key)
        if value in (None, "", [], {}):
            continue
        rendered = _truncate_hint(str(value))
        if not rendered or rendered in seen:
            continue
        seen.add(rendered)
        hints.append(rendered)
        if len(hints) >= 2:
            break
    return " · ".join(hints)


def _format_gathering_progress_line(
    tool_name: str,
    tool_input: Any,
    *,
    repeat_index: int,
) -> str:
    """Soft status line — source name, not tool-method chatter.

    Prefer ``· checking PostHog…`` over
    ``· gathering via Posthog Mcp · list posthog tools…`` so the transcript
    reads like exploration progress, not an MCP debug log.
    """
    source, _label = resolve_tool_activity_labels(tool_name)
    display = source.strip()
    if repeat_index > 1:
        display = f"{display} ({repeat_index})"
    safe_display = escape(display)
    hint = _tool_input_hint(tool_input)
    if hint:
        return f"· checking {safe_display} — {escape(hint)}…"
    return f"· checking {safe_display}…"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text)} chars total]"


def _persist_tool_calls(session: Session, executed: list[tuple[Any, Any]]) -> None:
    """Record each gathered tool-call result into the session log.

    Arguments and results are redacted and bounded before writing; failures are
    swallowed so logging never breaks the turn.
    """
    from core.agent_harness.spi.defaults import default_session_store
    from infrastructure.observability.trace.redaction import redact_sensitive

    store = default_session_store()
    for tc, output in executed:
        with contextlib.suppress(Exception):
            body = (
                output
                if isinstance(output, str)
                else json.dumps(redact_sensitive(output), default=str)
            )
            arguments = (
                redact_sensitive(tc.input) if isinstance(tc.input, dict) else {"value": tc.input}
            )
            store.append_tool_call(
                session.session_id,
                tool=str(tc.name),
                arguments=arguments,
                result=_truncate(body, _MAX_PER_TOOL_CHARS),
                ok=not (isinstance(output, dict) and "error" in output),
            )


class ShellGatherProgress:
    """Prints one dim line per gather tool call to the turn's console.

    :class:`~core.agent_harness.ports.ConsoleBindable`: the REPL streams each
    turn through a fresh spinner-aware console, so ``bind_turn`` retargets it.
    Keyed by display source (PostHog), not MCP method name — ``list_*`` and
    ``call_*`` otherwise both print identical ``· checking Posthog…`` lines.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._source_call_counts: dict[str, int] = {}

    def bind_console(self, console: Any) -> None:
        self._console = console
        self._source_call_counts = {}

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_start":
            name = str(data.get("name", "")).strip() or "tool"
            source, _label = resolve_tool_activity_labels(name)
            count_key = source.strip() or name
            self._source_call_counts[count_key] = self._source_call_counts.get(count_key, 0) + 1
            line = _format_gathering_progress_line(
                name,
                data.get("input"),
                repeat_index=self._source_call_counts[count_key],
            )
            self._console.print(f"[{DIM}]{line}[/]")
        elif kind == "gather_cancelled":
            self._console.print(f"[{DIM}]· gathering cancelled[/]")


def shell_gather_phase(session: Session, console: Console) -> GatherPhase:
    """The REPL's gather phase: live progress on ``console``, tool calls persisted to ``session``."""

    def persist(executed: list[tuple[Any, Any]]) -> None:
        _persist_tool_calls(session, executed)

    return GatherPhase(on_progress=ShellGatherProgress(console), persist=persist)


__all__ = ["ShellGatherProgress", "shell_gather_phase"]
