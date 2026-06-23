"""ReAct investigation agent — the core think → call tools → observe loop."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from app.agent.stages.investigate.loop import (
    InvestigationToolCallCache,
    degraded_investigation_from_llm_failure,
    duplicate_call_result,
    tool_call_signature,
)
from app.agent.stages.investigate.prompt import build_system_prompt, format_alert_context
from app.agent.stages.investigate.tools import (
    MAX_STAGNANT_ITERATIONS,
    STAGNATION_NUDGE,
    build_connected_tool_context,
    build_seed_calls,
    get_available_tools,
    merge_tool_evidence,
    tool_event_payload,
)
from app.agent.tool_loop import (
    AgentEventCallback,
    _build_assistant_msg,
    _build_synthetic_assistant_tool_call_msg,
    _build_tool_result_messages,
    _context_budget_ceiling_for_model,
    _enforce_context_budget,
    _run_parallel,
    _summarise,
    _tool_source,
)
from app.agent.utils.llm_invoke_errors import classify_llm_invoke_failure
from app.cli.interactive_shell.ui.output import debug_print, get_tracker
from app.constants.investigation import MAX_INVESTIGATION_LOOPS
from app.services.agent_llm_client import ToolCall, get_agent_llm
from app.state.evidence import EvidenceEntry
from app.tools.registered_tool import RegisteredTool
from app.utils.tool_trace import redact_sensitive

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
        state: dict[str, Any],
        on_event: AgentEventCallback | None = None,
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

        resolved = state.get("resolved_integrations") or {}
        tools = _tools_for_plan(self._filter_tools(get_available_tools(resolved)), state)
        tool_context = build_connected_tool_context(resolved, tools)
        state["available_sources"] = tool_context["available_sources"]
        state["available_action_names"] = tool_context["available_action_names"]

        if not tools:
            logger.warning("No tools available for investigation")

        llm = get_agent_llm()
        tool_schemas = llm.tool_schemas(tools)

        system = self._build_system_prompt(state)
        alert_text = format_alert_context(state)
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

        seed_calls = build_seed_calls(state, tools, llm)
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
            seed_results = _run_parallel(seed_calls, tools, resolved)
            seed_msgs = _build_tool_result_messages(llm, seed_calls, seed_results)

            seed_assistant_msg = _build_synthetic_assistant_tool_call_msg(llm, seed_calls)
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
                        source=_tool_source(tools, tc.name),
                        loop_iteration=-1,
                    )
                )
                _record_tool_end(tc, output)
                debug_print(f"[seed:{tc.name}] → {_summarise(output)}")

        context_ceiling = _context_budget_ceiling_for_model(getattr(llm, "_model", None))
        stagnant_iterations = 0
        force_conclusion = False
        for iteration in range(MAX_INVESTIGATION_LOOPS):
            logger.debug("[agent] iteration=%d", iteration)
            _emit("llm_start", {"iteration": iteration})
            active_tool_schemas: list[dict[str, Any]] = [] if force_conclusion else tool_schemas
            _enforce_context_budget(
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

            messages.append(_build_assistant_msg(llm, response))

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

            fresh_results = iter(_run_parallel(fresh_calls, tools, resolved) if fresh_calls else [])
            results: list[Any] = []
            for tc, cached_entry in zip(response.tool_calls, cached_entries, strict=True):
                if cached_entry is not None:
                    results.append(duplicate_call_result(tc, cached_entry))
                    continue
                output = next(fresh_results)
                tool_call_cache.store(tool_call_signature(tc), output, loop_iteration=iteration)
                results.append(output)

            tool_result_messages = _build_tool_result_messages(llm, response.tool_calls, results)
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
                        source=_tool_source(tools, tc.name),
                        loop_iteration=iteration,
                    )
                )
                _record_tool_end(tc, output)
                debug_print(f"[{tc.name}] → {_summarise(output)}")

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
