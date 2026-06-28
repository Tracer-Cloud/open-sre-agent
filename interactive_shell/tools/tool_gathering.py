"""Live tool-gathering pass for the interactive-shell assistant (terminal adapter).

The bounded think -> call-tools -> observe loop lives in the decoupled
:func:`core.agent.evidence_agent.gather_tool_evidence`. This module is the terminal adapter:
it renders each gathering step to the console and persists the gathered tool
calls into the shell's session storage, then hands the collected observation back
to :func:`interactive_shell.agent_shell.core.agent.answer_cli_agent`.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from rich.console import Console
from rich.markup import escape

from core.agent import evidence_agent
from interactive_shell.session import ReplSession
from interactive_shell.ui import DIM
from interactive_shell.ui.output.tool_details import tool_short_label, tool_source_label
from interactive_shell.utils.error_handling.exception_reporting import report_exception

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


class _ShellGatherErrorReporter:
    """Minimal :class:`core.agent.ports.ErrorReporter` over ``report_exception``."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        report_exception(exc, context=context, expected=expected)


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
    source = tool_source_label(tool_name)
    label = tool_short_label(tool_name, source)
    call_display = f"{source} · {label}" if label else source
    if repeat_index > 1:
        call_display = f"{call_display} ({repeat_index})"
    safe_display = escape(call_display)
    hint = _tool_input_hint(tool_input)
    if hint:
        return f"· gathering via {safe_display} — {escape(hint)}…"
    return f"· gathering via {safe_display}…"


def _resolve_gather_integrations(session: ReplSession, message: str) -> dict[str, Any]:
    """Resolve gather integrations through the decoupled agent helper."""
    return evidence_agent._resolve_gather_integrations(session, message)  # noqa: SLF001


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text)} chars total]"


def _persist_tool_calls(session: ReplSession, executed: list[tuple[Any, Any]]) -> None:
    """Record each gathered tool-call result into the session log.

    Arguments and results are redacted and bounded before writing; failures are
    swallowed so logging never breaks the turn.
    """
    from interactive_shell.session import default_session_storage
    from platform.observability.tool_trace import redact_sensitive

    storage = default_session_storage()
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
            storage.append_tool_call(
                session.session_id,
                tool=str(tc.name),
                arguments=arguments,
                result=_truncate(body, _MAX_PER_TOOL_CHARS),
                ok=not (isinstance(output, dict) and "error" in output),
            )


def gather_tool_evidence(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    is_tty: bool | None = None,
) -> str | None:
    """Run a bounded tool-calling loop and return collected evidence, or None.

    Returns a formatted observation block when at least one tool was executed;
    otherwise ``None`` so the caller falls back to the normal text-only answer.
    """
    tool_call_counts: dict[str, int] = {}

    def on_progress(kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_start":
            name = str(data.get("name", "")).strip() or "tool"
            tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
            line = _format_gathering_progress_line(
                name,
                data.get("input"),
                repeat_index=tool_call_counts[name],
            )
            console.print(f"[{DIM}]{line}[/]")
        elif kind == "gather_cancelled":
            console.print(f"[{DIM}]· gathering cancelled[/]")

    def persist(executed: list[tuple[Any, Any]]) -> None:
        _persist_tool_calls(session, executed)

    return evidence_agent.gather_tool_evidence(
        message,
        session,
        on_progress=on_progress,
        persist=persist,
        error_reporter=_ShellGatherErrorReporter(),
        is_tty=is_tty,
    )


__all__ = ["gather_tool_evidence"]
