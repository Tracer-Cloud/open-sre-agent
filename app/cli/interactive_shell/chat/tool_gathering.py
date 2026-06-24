"""Live tool-gathering pass for the interactive-shell assistant.

The REPL's conversational assistant (:func:`app.cli.interactive_shell.chat.cli_agent.answer_cli_agent`)
is grounded text generation — it cannot reach integrations on its own. This
module gives a free-form turn access to the **same registered tools the
investigation pipeline uses**: it runs a bounded think → call-tools → observe
loop (:func:`app.agent.tool_loop.run_tool_calling_loop`) over the available
``"investigation"`` surface tools, then hands the collected tool outputs back to
``answer_cli_agent`` as an observation block so it can compose a grounded answer.

Design notes:

* Tools are read-only data fetches, so calls run autonomously (no per-call
  confirmation) exactly like the investigation agent — see the routing decision
  recorded for this feature.
* When no integrations are configured (no tools available), gathering is a fast
  no-op and the normal text-only assistant path runs unchanged.
* Integration resolution is cached on the session so repeated turns don't
  re-resolve or re-render progress.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.agent.utils.alert_source import SECONDARY_TOOL_SOURCES
from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.ui import DIM
from app.cli.interactive_shell.ui.output.tool_details import tool_short_label, tool_source_label

# Keep the gathering loop short: this runs inline on a REPL turn, so it must stay
# responsive. A handful of iterations is enough to fetch the data needed to
# answer a question; the full multi-stage ReAct budget belongs to investigations.
_MAX_GATHER_ITERATIONS = 4

# Caps so a chatty tool (or many tools) can't blow up the follow-up prompt the
# assistant must summarize.
_MAX_OBSERVATION_CHARS = 12_000
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


def _resolve_session_integrations(session: ReplSession) -> dict[str, Any]:
    """Resolve integration configs once per session and cache the result."""
    if session.resolved_integrations_cache is not None:
        return session.resolved_integrations_cache

    from app.agent.stages.resolve_integrations import resolve_integrations

    resolved = resolve_integrations({})  # type: ignore[arg-type]  # env/store resolution path
    session.resolved_integrations_cache = resolved
    return resolved


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text)} chars total]"


def _format_observation(executed: list[tuple[Any, Any]]) -> str:
    """Render executed (tool_call, output) pairs into a compact prompt block."""
    blocks: list[str] = []
    for tc, output in executed:
        args = json.dumps(tc.input, default=str, sort_keys=True)
        body = output if isinstance(output, str) else json.dumps(output, default=str)
        blocks.append(
            f"Tool: {tc.name}\nArguments: {args}\nResult: {_truncate(body, _MAX_PER_TOOL_CHARS)}"
        )
    return _truncate("\n\n".join(blocks), _MAX_OBSERVATION_CHARS)


def _persist_tool_calls(session: ReplSession, executed: list[tuple[Any, Any]]) -> None:
    """Record each gathered tool-call result into the session log.

    Closes the observability gap where a turn's actual integration/API evidence
    was never persisted (only the final prose answer was). Arguments and results
    are redacted and bounded before writing; failures are swallowed so logging
    never breaks the turn.
    """
    from app.cli.interactive_shell.sessions.store import SessionStore
    from app.utils.tool_trace import redact_sensitive

    for tc, output in executed:
        with contextlib.suppress(Exception):
            ok = not (isinstance(output, dict) and "error" in output)
            body = (
                output
                if isinstance(output, str)
                else json.dumps(redact_sensitive(output), default=str)
            )
            arguments = (
                redact_sensitive(tc.input) if isinstance(tc.input, dict) else {"value": tc.input}
            )
            SessionStore.append_tool_call(
                session.session_id,
                tool=str(tc.name),
                arguments=arguments,
                result=_truncate(body, _MAX_PER_TOOL_CHARS),
                ok=ok,
            )


def _build_gather_system_prompt(session: ReplSession) -> str:
    configured = (
        ", ".join(session.configured_integrations)
        if session.configured_integrations
        else "(unknown)"
    )
    return (
        "You are the data-gathering step of the OpenSRE terminal assistant. The "
        "user asked a question that may be answerable with live data from the "
        "connected integrations. You have access to the same tools the "
        "investigation pipeline uses (logs, metrics, GitHub, error trackers, "
        "cloud APIs, etc.).\n"
        "Call the tools needed to gather evidence relevant to the user's "
        "question. Derive arguments (such as owner/repo, service names, time "
        "ranges, or search queries) from the user's message. Make tool calls "
        "ONLY when they will help answer the question; if no tool is relevant, "
        "respond with a short plain-text note and call nothing.\n"
        "Do NOT write the final user-facing answer here — a later step composes "
        "that from the tool results you collect. Stop calling tools as soon as "
        "you have enough data.\n"
        f"Configured integrations in this session: {configured}."
    )


def gather_tool_evidence(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    is_tty: bool | None = None,  # noqa: ARG001 — reserved for parity with answer agents
) -> str | None:
    """Run a bounded tool-calling loop and return collected evidence, or None.

    Returns a formatted observation block when at least one tool was executed;
    otherwise ``None`` so the caller falls back to the normal text-only answer.
    Any failure is reported and swallowed (returns ``None``) — gathering must
    never break the conversational turn.
    """
    try:
        from app.agent.stages.investigate.tools import get_available_tools
        from app.agent.tool_loop import run_tool_calling_loop
        from app.services.agent_llm_client import get_agent_llm

        resolved = _resolve_session_integrations(session)
        tools = get_available_tools(resolved)
        if not tools:
            return None
        if not any(str(tool.source) not in SECONDARY_TOOL_SOURCES for tool in tools):
            return None

        try:
            llm = get_agent_llm()
        except Exception as exc:
            # Tool-calling client unavailable (e.g. unsupported provider): fall
            # back to the text-only assistant rather than failing the turn.
            report_exception(exc, context="interactive_shell.tool_gathering.client", expected=True)
            return None

        tool_call_counts: dict[str, int] = {}

        def _on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_start":
                name = str(data.get("name", "")).strip() or "tool"
                tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
                line = _format_gathering_progress_line(
                    name,
                    data.get("input"),
                    repeat_index=tool_call_counts[name],
                )
                console.print(f"[{DIM}]{line}[/]")

        result = run_tool_calling_loop(
            llm=llm,
            system=_build_gather_system_prompt(session),
            messages=[{"role": "user", "content": message}],
            tools=tools,
            resolved_integrations=resolved,
            max_iterations=_MAX_GATHER_ITERATIONS,
            on_event=_on_event,
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]· gathering cancelled[/]")
        return None
    except Exception as exc:
        report_exception(exc, context="interactive_shell.tool_gathering")
        return None

    if not result.executed:
        return None
    _persist_tool_calls(session, result.executed)
    return _format_observation(result.executed)


__all__ = ["gather_tool_evidence"]
