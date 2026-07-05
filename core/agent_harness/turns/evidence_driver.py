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
from typing import Any, Protocol

from core.agent import Agent
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.debug.prompt_trace import persist_turn_system_prompt
from core.agent_harness.integrations.resolution import resolve_and_cache_integrations
from core.agent_harness.ports import ErrorReporter, SessionStore, ToolEventObserver
from core.agent_harness.prompts.conversation_memory import (
    NO_HISTORY_PLACEHOLDER,
    format_recent_conversation,
)
from core.agent_harness.prompts.gather import build_gather_system_prompt
from core.domain.alerts.alert_source import SECONDARY_TOOL_SOURCES
from core.events import runtime_event_callback_from_observer
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
    ) -> Agent[Any]: ...


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
    """Resolve integrations for one gather turn, enriching GitHub repo scope when inferred.

    ``resolved_integrations`` is the turn's already-resolved view (from
    ``TurnSnapshot``); when supplied it is used as the base instead of resolving
    again, so the gather phase agrees with the action prompt and tools about what
    is connected. GitHub repo scope is still applied on top.
    """
    base = (
        dict(resolved_integrations)
        if resolved_integrations is not None
        else resolve_and_cache_integrations(session)
    )
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


def _has_usable_gather_tools(gather_tools: list[Any]) -> bool:
    """True iff at least one non-secondary-source tool is available.

    Lets callers early-abort before paying for the LLM client + Agent.run
    set-up costs.
    """
    if not gather_tools:
        return False
    return any(str(t.source) not in SECONDARY_TOOL_SOURCES for t in gather_tools)


def _load_gather_llm_or_none(error_reporter: ErrorReporter | None) -> Any | None:
    """Load the tool-calling LLM; return None (with expected=True) on failure.

    The evidence turn must never break the conversation: when the tool-calling
    client isn't available (unsupported provider, misconfig), the caller
    surfaces a controlled fallback rather than a hard error.
    """
    from core.llm.agent_llm_client import get_agent_llm

    return _safe_execute(
        get_agent_llm,
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
) -> Agent[Any]:
    """Build the Agent for one evidence-gather turn."""
    config = AgentConfig(
        llm=llm,
        system=build_gather_system_prompt(session),
        tools=tuple(gather_tools),
        resolved_integrations=resolved,
        max_iterations=_MAX_GATHER_ITERATIONS,
        on_runtime_event=runtime_event_callback_from_observer(on_progress),
    )
    return build_agent(config)


def gather_tool_evidence(
    message: str,
    session: SessionStore,
    *,
    on_progress: ToolEventObserver | None = None,
    persist: PersistToolCalls | None = None,
    error_reporter: ErrorReporter | None = None,
    is_tty: bool | None = None,  # noqa: ARG001 — reserved for parity with answer agents
    agent_factory: GatherAgentFactory | None = None,
    resolved_integrations: dict[str, Any] | None = None,
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
        from tools.investigation.stages.gather_evidence.tools import get_available_tools

        resolved = _resolve_gather_integrations(
            session, message, resolved_integrations=resolved_integrations
        )
        gather_tools = list(get_available_tools(resolved))
        if not _has_usable_gather_tools(gather_tools):
            return None
        llm = _load_gather_llm_or_none(error_reporter)
        if llm is None:
            return None
        build_agent_for_turn = agent_factory or _build_evidence_agent
        agent = build_agent_for_turn(
            llm=llm,
            session=session,
            gather_tools=gather_tools,
            resolved=resolved,
            on_progress=on_progress,
        )
        result = agent.run(
            [{"role": "user", "content": _build_gather_user_message(session, message)}]
        )
        persist_turn_system_prompt(
            session,
            phase="gather_agent",
            system_prompt=result.final_system_prompt,
        )
        return result

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
        return None

    if result is None:
        return None

    if not result.executed:
        return None
    if persist is not None:
        persist(result.executed)
    return _format_observation(result.executed)


__all__ = ["GatherAgentFactory", "PersistToolCalls", "gather_tool_evidence"]
