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
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

from core.agent import Agent
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.ports import ErrorReporter, SessionStore, ToolEventObserver
from core.agent_harness.prompts.gather import build_gather_system_prompt
from core.agent_harness.prompts.memory.conversation import (
    NO_HISTORY_PLACEHOLDER,
    format_recent_conversation,
)
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations
from core.agent_harness.turns.goal_review import build_gather_goal_reviewer
from core.agent_harness.turns.source_circuit_breaker import SourceCircuitBreaker
from core.domain.alerts.alert_source import secondary_tool_sources
from core.events import runtime_event_callback_from_observer
from core.state import MAX_CONVERSATION_MESSAGES
from platform.analytics.react_turn import run_react_agent_with_telemetry
from platform.harness_ports import enrich_resolved_with_repo_scopes
from platform.observability.trace.prompts import persist_turn_system_prompt
from platform.observability.trace.spans import component_span

log = logging.getLogger(__name__)

# Keep the gathering loop short: this runs inline on a turn, so it must stay
# responsive. The budget must still fit an MCP-bridged source's full path —
# list tools -> fetch a schema -> call the real tool -> conclude — plus one
# goal-review nudge; 4 was observed live to cap out on discovery alone, so the
# PostHog count query never ran. The full multi-stage ReAct budget belongs to
# investigations. Headless metric reports (PostHog / digests) may raise this
# via ``max_iterations``.
_MAX_GATHER_ITERATIONS = 6
MAX_REPORT_GATHER_ITERATIONS = 12

# Caps so a chatty tool (or many tools) can't blow up the follow-up prompt the
# assistant must summarize.
_MAX_OBSERVATION_CHARS = 12_000
_MAX_PER_TOOL_CHARS = 4_000

# A persistence sink for gathered tool calls: ``persist(executed)`` where
# ``executed`` is a list of ``(tool_call, output)`` pairs.
PersistToolCalls = Callable[[list[tuple[Any, Any]]], None]


class GatherAgentFactory(Protocol):
    """Build the runtime :class:`Agent` for one evidence-gather turn."""

    def __call__(
        self,
        *,
        llm: Any,
        session: SessionStore,
        gather_tools: list[Any],
        resolved: dict[str, Any],
        on_progress: ToolEventObserver | None,
        message: str,
        max_iterations: int = _MAX_GATHER_ITERATIONS,
    ) -> Agent[Any]:
        """Build and return the evidence-gather agent for one turn."""


class AgentExecutionError(RuntimeError):
    """Base class for failures swallowed to preserve the conversational turn."""

    def __init__(self, message: str, *, cause: BaseException) -> None:
        super().__init__(message)
        self.cause = cause


class GatherLlmLoadError(AgentExecutionError):
    """Evidence gather LLM loading failed, so the turn falls back gracefully."""


class GatherEvidenceExecutionError(AgentExecutionError):
    """Bounded evidence gathering failed, so the turn falls back gracefully."""


def _safe_execute[T](
    operation: Callable[[], T],
    *,
    error_reporter: ErrorReporter | None,
    context: str,
    wrap_error: Callable[[BaseException], AgentExecutionError],
    expected: bool = False,
) -> T | None:
    """Run ``operation`` through the one allowed broad-catch fallback boundary."""

    try:
        return operation()
    except Exception as exc:  # noqa: BLE001 - centralized turn-safe fallback boundary
        wrapped = wrap_error(exc)
        if error_reporter is not None:
            error_reporter.report(wrapped.cause, context=context, expected=expected)
        return None


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


def _resolve_gather_integrations(
    session: SessionStore,
    message: str,
    resolved_integrations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve integrations for one gather turn, enriching repository scope when inferred.

    ``resolved_integrations`` is the turn's already-resolved view (from
    ``TurnSnapshot``); when supplied it is used as the base instead of resolving
    again, so the gather phase agrees with the action prompt and tools about what
    is connected. VCS repo scope is still applied on top via the harness port.
    """
    base = (
        dict(resolved_integrations)
        if resolved_integrations is not None
        else resolve_and_cache_integrations(session)
    )

    def _set_cached_scope(vendor: str, scope: tuple[str, ...] | None) -> None:
        scopes = dict(session.vcs_repo_scopes)
        if scope is None:
            scopes.pop(vendor, None)
        else:
            scopes[vendor] = scope
        session.vcs_repo_scopes = scopes

    return enrich_resolved_with_repo_scopes(
        resolved=base,
        message=message,
        conversation_messages=session.cli_agent_messages,
        env=os.environ,
        cwd=os.getcwd(),
        cached_scopes=dict(session.vcs_repo_scopes),
        set_cached_scope=_set_cached_scope,
    )


def _build_gather_user_message(session: SessionStore, message: str) -> str:
    messages = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]
    history = format_recent_conversation(messages, max_turns=3)
    if history == NO_HISTORY_PLACEHOLDER:
        return message
    return f"Recent conversation:\n{history}\n\nCurrent question:\n{message}"


def _has_usable_gather_tools(gather_tools: list[Any]) -> bool:
    """True iff at least one non-secondary-source tool is available.

    Lets callers early-abort before paying for the LLM client + Agent.run
    set-up costs.
    """
    if not gather_tools:
        return False
    secondary = secondary_tool_sources()
    return any(str(t.source) not in secondary for t in gather_tools)


def _load_gather_llm_or_none(error_reporter: ErrorReporter | None) -> Any | None:
    """Load the tool-calling LLM; return None (with expected=True) on failure.

    The evidence turn must never break the conversation: when the tool-calling
    client isn't available (unsupported provider, misconfig), the caller
    surfaces a controlled fallback rather than a hard error.
    """
    from core.llm.factory import LLMRole, get_llm

    return _safe_execute(
        lambda: get_llm(LLMRole.AGENT),
        error_reporter=error_reporter,
        context="core.agent_harness.turns.evidence_driver.client",
        wrap_error=lambda exc: GatherLlmLoadError(
            "Failed to load the evidence-gather LLM client.",
            cause=exc,
        ),
        expected=True,
    )


def _build_evidence_agent(
    *,
    llm: Any,
    session: SessionStore,
    gather_tools: list[Any],
    resolved: dict[str, Any],
    on_progress: ToolEventObserver | None,
    message: str,
    max_iterations: int = _MAX_GATHER_ITERATIONS,
    tool_resources: dict[str, Any] | None = None,
) -> Agent[Any]:
    """Build the Agent for one evidence-gather turn.

    The turn gets a gather-flavored goal reviewer over the user's question:
    when the loop concludes with only discovery output (e.g. an MCP tool
    listing but no actual query result), one review verdict nudges it to keep
    gathering instead of handing the summarizer a dead end. The action-turn
    reviewer would accept those conclusions — its prompt treats an honest
    report of findings as reached — so the gather flavor reviews against
    "did the data actually get fetched" instead.
    """
    config = AgentConfig(
        llm=llm,
        system=build_gather_system_prompt(session),
        tools=tuple(gather_tools),
        resolved_integrations=resolved,
        max_iterations=max_iterations,
        tool_hooks=SourceCircuitBreaker().hooks(),
        on_runtime_event=runtime_event_callback_from_observer(on_progress),
        goal=build_gather_goal_reviewer(llm, message),
        tool_resources=dict(tool_resources or {}),
    )
    return build_agent(config)


def gather_tool_evidence(
    message: str,
    session: SessionStore,
    *,
    on_progress: ToolEventObserver | None = None,
    persist: PersistToolCalls | None = None,
    error_reporter: ErrorReporter | None = None,
    agent_factory: GatherAgentFactory | None = None,
    resolved_integrations: dict[str, Any] | None = None,
    max_iterations: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> str | None:
    """Run a bounded tool-calling loop and return collected evidence, or None.

    Returns a formatted observation block when at least one tool was executed;
    otherwise ``None`` so the caller falls back to the normal text-only answer.
    Any failure is reported and swallowed (returns ``None``) — gathering must
    never break the conversational turn.
    """

    def _run_gather_turn() -> Any | None:
        # Tool discovery, integration resolution, and LLM load run inside this
        # helper, within the ``_safe_execute`` fallback boundary.
        from core.agent_harness.turns.host_cancel import cancel_tool_resources
        from platform.harness_ports import get_investigation_tools

        if is_cancelled is not None and is_cancelled():
            log.debug("gather_evidence skip: host cancelled")
            return None
        resolved = _resolve_gather_integrations(
            session, message, resolved_integrations=resolved_integrations
        )
        gather_tools = list(get_investigation_tools(resolved))
        if not _has_usable_gather_tools(gather_tools):
            log.debug("gather_evidence skip: no usable tools")
            return None
        llm = _load_gather_llm_or_none(error_reporter)
        if llm is None:
            log.debug("gather_evidence skip: LLM unavailable")
            return None
        iteration_cap = (
            _MAX_GATHER_ITERATIONS if max_iterations is None else max(1, int(max_iterations))
        )
        log.debug(
            "gather_evidence start tools=%s integrations=%s iterations=%s",
            len(gather_tools),
            len(resolved),
            iteration_cap,
        )
        tool_resources = cancel_tool_resources(is_cancelled)
        if agent_factory is None:
            agent = _build_evidence_agent(
                llm=llm,
                session=session,
                gather_tools=gather_tools,
                resolved=resolved,
                on_progress=on_progress,
                message=message,
                max_iterations=iteration_cap,
                tool_resources=tool_resources,
            )
        else:
            # Test/custom factories keep the historical signature; cancel probe
            # still short-circuits before this path via ``is_cancelled`` above.
            agent = agent_factory(
                llm=llm,
                session=session,
                gather_tools=gather_tools,
                resolved=resolved,
                on_progress=on_progress,
                message=message,
                max_iterations=iteration_cap,
            )
        result = run_react_agent_with_telemetry(
            agent,
            [{"role": "user", "content": _build_gather_user_message(session, message)}],
            phase="gather",
            iteration_cap=iteration_cap,
            llm=llm,
            session=session,
        )
        persist_turn_system_prompt(
            session,
            phase="gather_agent",
            system_prompt=result.final_system_prompt,
        )
        return result

    with component_span("gather_evidence", session_id=getattr(session, "session_id", None)):
        try:
            result = _safe_execute(
                _run_gather_turn,
                error_reporter=error_reporter,
                context="core.agent_harness.turns.evidence_driver",
                wrap_error=lambda exc: GatherEvidenceExecutionError(
                    "Failed to gather evidence for the current conversational turn.",
                    cause=exc,
                ),
            )
        except KeyboardInterrupt:
            if on_progress is not None:
                on_progress("gather_cancelled", {})
            log.debug("gather_evidence cancelled")
            return None

        if result is None:
            return None
        if not result.executed:
            log.debug("gather_evidence done: no tools executed")
            return None
        if persist is not None:
            persist(result.executed)
        log.debug("gather_evidence done tools_executed=%s", len(result.executed))
        return _format_observation(result.executed)


__all__ = [
    "GatherAgentFactory",
    "MAX_REPORT_GATHER_ITERATIONS",
    "PersistToolCalls",
    "gather_tool_evidence",
]
