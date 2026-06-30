"""Bounded evidence-gather pass for the conversational assistant.

The assistant is grounded text generation — it cannot reach integrations on its
own. This module gives a free-form turn access to the **same registered tools
the investigation pipeline uses**: it runs a bounded think -> call-tools ->
observe loop (:class:`core.agent.Agent`) over the available
``"investigation"`` surface tools, then returns the collected tool outputs as an
observation block the assistant can summarize.

Decoupled from any terminal: progress is forwarded through an optional
``on_progress`` observer and persistence through an optional ``persist`` callback
(the shell adapter renders the progress line and writes to its session storage).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from core.agent_harness.conversation_memory import (
    NO_HISTORY_PLACEHOLDER,
    format_recent_conversation,
)
from core.agent_harness.ports import ErrorReporter, SessionStore, ToolEventObserver
from core.agent_harness.session.integrations_cache import (
    has_only_runtime_metadata,
    has_resolved_integrations,
    merge_resolved_integrations,
)
from core.domain.alerts.alert_source import SECONDARY_TOOL_SOURCES
from integrations.github.repo_scope import (
    apply_github_repo_scope,
    infer_github_repo_scope,
)

# Keep the gathering loop short: this runs inline on a turn, so it must stay
# responsive. A handful of iterations is enough to fetch the data needed to
# answer a question; the full multi-stage ReAct budget belongs to investigations.
_MAX_GATHER_ITERATIONS = 4

# Caps so a chatty tool (or many tools) can't blow up the follow-up prompt the
# assistant must summarize.
_MAX_OBSERVATION_CHARS = 12_000
_MAX_PER_TOOL_CHARS = 4_000

# A persistence sink for gathered tool calls: ``persist(executed)`` where
# ``executed`` is a list of ``(tool_call, output)`` pairs.
PersistToolCalls = Callable[[list[tuple[Any, Any]]], None]


def _resolve_session_integrations(session: SessionStore) -> dict[str, Any]:
    """Resolve integration configs once per session and cache the result."""
    cached = session.resolved_integrations_cache
    if cached is not None and (
        has_resolved_integrations(cached) or not has_only_runtime_metadata(cached)
    ):
        return cached

    from tools.investigation.stages.resolve_integrations import resolve_integrations

    updates = resolve_integrations({})  # type: ignore[arg-type]  # env/store resolution path
    resolved = dict(updates.get("resolved_integrations") or {})
    if resolved:
        session.resolved_integrations_cache = merge_resolved_integrations(
            cached,
            resolved,
        )
    return session.resolved_integrations_cache or {}


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


def _build_gather_system_prompt(session: SessionStore) -> str:
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
        "For GitHub repository metadata such as star count, forks, visibility, "
        "or default branch, call get_github_repository — do not use "
        "search_github_code or search_github_issues for those questions.\n"
        "Do NOT write the final user-facing answer here — a later step composes "
        "that from the tool results you collect. Stop calling tools as soon as "
        "you have enough data.\n"
        f"Configured integrations in this session: {configured}."
    )


def _resolve_gather_integrations(session: SessionStore, message: str) -> dict[str, Any]:
    """Resolve integrations for one gather turn, enriching GitHub repo scope when inferred."""
    base = _resolve_session_integrations(session)
    scope = infer_github_repo_scope(
        message=message,
        conversation_messages=session.cli_agent_messages,
        env=os.environ,
        cwd=os.getcwd(),
        cached=session.github_repo_scope,
    )
    if scope:
        session.github_repo_scope = scope
        return apply_github_repo_scope(base, scope[0], scope[1])
    return base


def _build_gather_user_message(session: SessionStore, message: str) -> str:
    messages = session.cli_agent_messages[-24:]
    history = format_recent_conversation(messages, max_turns=3)
    if history == NO_HISTORY_PLACEHOLDER:
        return message
    return f"Recent conversation:\n{history}\n\nCurrent question:\n{message}"


def gather_tool_evidence(
    message: str,
    session: SessionStore,
    *,
    on_progress: ToolEventObserver | None = None,
    persist: PersistToolCalls | None = None,
    error_reporter: ErrorReporter | None = None,
    is_tty: bool | None = None,  # noqa: ARG001 — reserved for parity with answer agents
) -> str | None:
    """Run a bounded tool-calling loop and return collected evidence, or None.

    Returns a formatted observation block when at least one tool was executed;
    otherwise ``None`` so the caller falls back to the normal text-only answer.
    Any failure is reported and swallowed (returns ``None``) — gathering must
    never break the conversational turn.
    """
    try:
        from core.agent import Agent
        from core.events import RuntimeEvent, legacy_callback_payload
        from core.llm.agent_llm_client import get_agent_llm
        from tools.investigation.stages.gather_evidence.tools import get_available_tools

        resolved = _resolve_gather_integrations(session, message)
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
            if error_reporter is not None:
                error_reporter.report(
                    exc, context="core.agent_harness.evidence_agent.client", expected=True
                )
            return None

        def on_runtime_event(event: RuntimeEvent) -> None:
            if on_progress is None:
                return
            legacy = legacy_callback_payload(event)
            if legacy is not None:
                on_progress(*legacy)

        result = Agent(
            llm=llm,
            system=_build_gather_system_prompt(session),
            tools=tools,
            resolved_integrations=resolved,
            max_iterations=_MAX_GATHER_ITERATIONS,
            on_runtime_event=on_runtime_event,
        ).run([{"role": "user", "content": _build_gather_user_message(session, message)}])
    except KeyboardInterrupt:
        if on_progress is not None:
            on_progress("gather_cancelled", {})
        return None
    except Exception as exc:
        if error_reporter is not None:
            error_reporter.report(exc, context="core.agent_harness.evidence_agent")
        return None

    if not result.executed:
        return None
    if persist is not None:
        persist(result.executed)
    return _format_observation(result.executed)


__all__ = ["PersistToolCalls", "gather_tool_evidence"]
