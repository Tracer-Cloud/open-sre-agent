"""ReAct investigation agent — the core think → call tools → observe loop."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, cast

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from core.domain.state import InvestigationState
from core.domain.state.evidence import EvidenceEntry
from core.orchestration.node.investigate.loop import (
    InvestigationToolCallCache,
    degraded_investigation_from_llm_failure,
    duplicate_call_result,
    tool_call_signature,
)
from core.orchestration.node.investigate.prompt import build_system_prompt, format_alert_context
from core.orchestration.node.investigate.tools import (
    MAX_STAGNANT_ITERATIONS,
    STAGNATION_NUDGE,
    build_connected_tool_context,
    build_seed_calls,
    get_available_tools,
    merge_tool_evidence,
    tool_event_payload,
)
from core.runtime import (
    LoopEventCallback,
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
    context_budget_ceiling_for_model,
    enforce_context_budget,
    execute_tools,
    summarise,
    tool_source,
)
from core.runtime.llm.agent_llm_client import ToolCall, get_agent_llm
from core.runtime.llm_invoke_errors import classify_llm_invoke_failure
from platform.observability import debug_print
from platform.observability import get_progress_tracker as get_tracker
from platform.observability.tool_trace import redact_sensitive
from tools.registered_tool import RegisteredTool

logger = logging.getLogger(__name__)


def _mark_messages(messages: list[dict[str, Any]], key: str) -> None:
    for msg in messages:
        msg[key] = True


def _tools_for_plan(tools: list[RegisteredTool], state: dict[str, Any]) -> list[RegisteredTool]:
    planned_raw = state.get("planned_actions")
    if not isinstance(planned_raw, list) or not planned_raw:
        return tools

    planned_names = [str(name) for name in planned_raw if str(name).strip()]
    if not planned_names:
        return tools

    by_name = {tool.name: tool for tool in tools}
    planned = [by_name[name] for name in planned_names if name in by_name]
    return planned or tools


class ConnectedInvestigationAgent:
    """ReAct loop scoped to the tools enabled by connected integrations."""

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — used by overrides
        iteration: int,  # noqa: ARG002 — used by overrides
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools."""
        return True, None

    def _filter_tools(
        self,
        tools: list[RegisteredTool],
    ) -> list[RegisteredTool]:
        """Hook: narrow the tool list the agent will see."""
        return tools

    def _build_system_prompt(self, state: dict[str, Any]) -> str:
        """Hook: produce the LLM system prompt for this investigation."""
        return build_system_prompt(state)

    def run(
        self,
        state: InvestigationState,
        on_event: LoopEventCallback | None = None,
    ) -> dict[str, Any]:
        """Run the full investigation. Returns a dict of state updates."""
        tracker = get_tracker()
        tracker.start("investigation_agent", "Running investigation agent loop")

        def _emit(kind: str, data: dict[str, Any]) -> None:
            if on_event is not None:
                with contextlib.suppress(Exception):
                    on_event(kind, data)

        def _record_tool_start(tc: ToolCall) -> None:
            tracker.record_tool_start(tc.name, redact_sensitive(tc.input), event_key=tc.id)
            _emit("tool_start", tool_event_payload(tc))

        def _record_tool_end(tc: ToolCall, output: Any) -> None:
            tracker.record_tool_end(
                tc.name,
                redact_sensitive(output),
                event_key=tc.id,
                tool_input=redact_sensitive(tc.input),
            )
            _emit("tool_end", tool_event_payload(tc, output=output))

        state_dict = cast(dict[str, Any], state)
        resolved = state.get("resolved_integrations") or {}
        tools = _tools_for_plan(self._filter_tools(get_available_tools(resolved)), state_dict)
        tool_context = build_connected_tool_context(resolved, tools)

        if not tools:
            logger.warning("No tools available for investigation")

        llm = get_agent_llm()
        tool_schemas = llm.tool_schemas(tools)

        # Merge tool_context into a local view so the system prompt can read
        # available_sources / available_action_names without mutating the caller's state.
        system = self._build_system_prompt({**state_dict, **tool_context})
        alert_text = format_alert_context(state_dict)
        messages: list[dict[str, Any]] = [{"role": "user", "content": alert_text}]

        evidence: dict[str, Any] = {}
        evidence_entries: list[EvidenceEntry] = []
        executed_hypotheses: list[dict[str, Any]] = []
        tool_call_cache = InvestigationToolCallCache()

        _emit(
            "agent_start",
            {
                "tool_count": len(tools),
                "connected_integrations": tool_context["connected_integrations"],
                "available_action_names": tool_context["available_action_names"],
            },
        )

        seed_calls = build_seed_calls(state_dict, tools, llm)
        if seed_calls:
            logger.debug("[agent] seeding %d primary tool calls before LLM loop", len(seed_calls))
            for tc in seed_calls:
                _record_tool_start(tc)
            executed_hypotheses.append(
                {
                    "hypothesis": "Seed primary integration tools",
                    "actions": [tc.name for tc in seed_calls],
                    "loop_iteration": -1,
                }
            )
            seed_results = execute_tools(seed_calls, tools, resolved)
            seed_msgs = build_tool_result_messages(llm, seed_calls, seed_results)

            seed_assistant_msg = build_synthetic_assistant_tool_call_message(llm, seed_calls)
            _mark_messages([seed_assistant_msg, *seed_msgs], "_opensre_seed")
            messages.append(seed_assistant_msg)
            messages.extend(seed_msgs)

            for tc, output in zip(seed_calls, seed_results):
                tool_call_cache.store(tool_call_signature(tc), output, loop_iteration=-1)
                merge_tool_evidence(evidence, tc.name, output, tc.input)
                evidence_entries.append(
                    EvidenceEntry(
                        key=tc.name,
                        data=redact_sensitive(output),
                        tool_name=tc.name,
                        tool_args=redact_sensitive(tc.input),
                        source=tool_source(tools, tc.name),
                        loop_iteration=-1,
                    )
                )
                _record_tool_end(tc, output)
                debug_print(f"[seed:{tc.name}] → {summarise(output)}")

        # Expose planned tools and live evidence to _should_accept_conclusion overrides.
        # Both are set before the loop so they're always initialised; evidence is a
        # reference to the same mutable dict the loop populates, so overrides always
        # see the current state without an extra assignment inside the loop body.
        # Only track names that exist in the tool schema the LLM actually receives —
        # hallucinated or expired names would otherwise cause futile nudge cycles.
        _available_tool_names = {t.name for t in tools}
        self._planned_actions: list[str] = [
            str(name)
            for name in (state_dict.get("planned_actions") or [])
            if str(name).strip() and str(name) in _available_tool_names
        ]
        self._current_evidence: dict[str, Any] = evidence

        context_ceiling = context_budget_ceiling_for_model(getattr(llm, "_model", None))
        stagnant_iterations = 0
        force_conclusion = False
        for iteration in range(MAX_INVESTIGATION_LOOPS):
            logger.debug("[agent] iteration=%d", iteration)
            _emit("llm_start", {"iteration": iteration})
            active_tool_schemas: list[dict[str, Any]] = [] if force_conclusion else tool_schemas
            enforce_context_budget(
                messages, system=system, tools=active_tool_schemas, ceiling=context_ceiling
            )
            try:
                response = llm.invoke(messages, system=system, tools=active_tool_schemas)

            except Exception as err:
                failure = classify_llm_invoke_failure(err)
                if failure is None:
                    raise
                return degraded_investigation_from_llm_failure(
                    failure,
                    err=err,
                    tracker=tracker,
                    _emit=_emit,
                    evidence=evidence,
                    evidence_entries=evidence_entries,
                    messages=messages,
                    executed_hypotheses=executed_hypotheses,
                    tool_context=tool_context,
                )

            messages.append(build_assistant_message(llm, response))

            if not response.has_tool_calls:
                accept, nudge = self._should_accept_conclusion(
                    evidence_count=len(evidence_entries),
                    iteration=iteration,
                )
                if accept:
                    logger.debug("[agent] no tool calls — done after %d iterations", iteration + 1)
                    break
                if nudge is None:
                    raise ValueError(
                        f"{type(self).__name__}._should_accept_conclusion returned "
                        "(False, None) — a nudge string is required when rejecting "
                        "the conclusion, otherwise the LLM will loop on an unchanged "
                        "message history until MAX_INVESTIGATION_LOOPS."
                    )
                messages.append({"role": "user", "content": nudge})
                continue

            cached_entries = [
                tool_call_cache.lookup(tool_call_signature(tc)) for tc in response.tool_calls
            ]
            duplicate_flags = [cached is not None for cached in cached_entries]
            fresh_calls = [
                tc
                for tc, cached in zip(response.tool_calls, cached_entries, strict=True)
                if cached is None
            ]
            for tc in fresh_calls:
                _record_tool_start(tc)

            executed_hypotheses.append(
                {
                    "hypothesis": f"Agent iteration {iteration}",
                    "actions": [tc.name for tc in fresh_calls],
                    "loop_iteration": iteration,
                }
            )

            fresh_results = iter(execute_tools(fresh_calls, tools, resolved) if fresh_calls else [])
            results: list[Any] = []
            for tc, cached_entry in zip(response.tool_calls, cached_entries, strict=True):
                if cached_entry is not None:
                    results.append(duplicate_call_result(tc, cached_entry))
                    continue
                output = next(fresh_results)
                tool_call_cache.store(tool_call_signature(tc), output, loop_iteration=iteration)
                results.append(output)

            tool_result_messages = build_tool_result_messages(llm, response.tool_calls, results)
            if duplicate_flags and all(duplicate_flags):
                _mark_messages([messages[-1], *tool_result_messages], "_opensre_duplicate_result")
            messages.extend(tool_result_messages)

            for tc, output, is_dup in zip(response.tool_calls, results, duplicate_flags):
                if is_dup:
                    debug_print(f"[{tc.name}] → duplicate call suppressed")
                    continue
                merge_tool_evidence(evidence, tc.name, output, tc.input)
                evidence_entries.append(
                    EvidenceEntry(
                        key=tc.name,
                        data=redact_sensitive(output),
                        tool_name=tc.name,
                        tool_args=redact_sensitive(tc.input),
                        source=tool_source(tools, tc.name),
                        loop_iteration=iteration,
                    )
                )
                _record_tool_end(tc, output)
                debug_print(f"[{tc.name}] → {summarise(output)}")

            if fresh_calls:
                stagnant_iterations = 0
            else:
                stagnant_iterations += 1
                messages.append({"role": "user", "content": STAGNATION_NUDGE})
                if stagnant_iterations >= MAX_STAGNANT_ITERATIONS:
                    logger.warning(
                        "[agent] %d consecutive duplicate-only iterations — forcing "
                        "tool-free conclusion before MAX_INVESTIGATION_LOOPS",
                        stagnant_iterations,
                    )
                    force_conclusion = True
        else:
            logger.warning(
                "[agent] hit MAX_INVESTIGATION_LOOPS=%d without finishing",
                MAX_INVESTIGATION_LOOPS,
            )

        _emit(
            "agent_end",
            {
                "evidence_count": len(evidence_entries),
                "message_count": len(messages),
            },
        )

        tracker.complete(
            "investigation_agent",
            fields_updated=["evidence", "evidence_entries", "agent_messages"],
            message=f"evidence:{len(evidence_entries)} messages:{len(messages)}",
        )

        updates = {
            "evidence": evidence,
            "evidence_entries": [e.model_dump() for e in evidence_entries],
            "agent_messages": messages,
            "executed_hypotheses": executed_hypotheses,
        }
        updates.update(tool_context)
        return updates


InvestigationAgent = ConnectedInvestigationAgent


def get_investigation_agent_class() -> type[ConnectedInvestigationAgent]:
    """Return the investigation agent class appropriate for the current LLM provider.

    Callers that need a fixed class (e.g. bench harness, integration tests) should
    pass an explicit ``agent_class`` to the pipeline rather than calling this.
    """
    from core.runtime.llm.agent_llm_client import CLIBackedAgentClient

    if isinstance(get_agent_llm(), CLIBackedAgentClient):
        return CLIBackedInvestigationAgent
    return ConnectedInvestigationAgent


class CLIBackedInvestigationAgent(ConnectedInvestigationAgent):
    """Investigation agent for CLI-backed LLMs (Codex, Claude Code CLI, etc.).

    CLI models receive the full conversation history flattened into a single
    text prompt per invoke. They tend to emit a plain-text final answer as
    soon as they see accumulated tool results, exiting the ReAct loop before
    all planned tools have been called.

    This subclass overrides the conclusion hook to nudge the model to call
    every planned tool before accepting its final answer. The outer
    MAX_INVESTIGATION_LOOPS cap still bounds worst-case runtime.
    """

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — base class signature
        iteration: int,
    ) -> tuple[bool, str | None]:
        planned = getattr(self, "_planned_actions", [])
        evidence = getattr(self, "_current_evidence", None)

        if not planned or evidence is None:
            return True, None

        # Leave room for a final text-only iteration after the nudge fires.
        if iteration >= MAX_INVESTIGATION_LOOPS - 2:
            return True, None

        uncalled = [name for name in planned if name not in evidence]
        if not uncalled:
            return True, None

        tool_list = ", ".join(uncalled)
        return False, (
            f"You have not yet called these planned investigation tools: {tool_list}. "
            "Call them now using the JSON tool_calls format before writing your final answer."
        )
