"""ReAct investigation agent — the core think → call tools → observe loop."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from app.agent.llm_invoke_errors import LLMInvokeFailure, classify_llm_invoke_failure
from app.agent.prompt import build_system_prompt, format_alert_context
from app.agent.result import InvestigationResult, parse_diagnosis
from app.agent.tool_loop import (
    AgentEventCallback,
    _build_assistant_msg,
    _build_synthetic_assistant_tool_call_msg,
    _build_tool_result_messages,
    _context_budget_ceiling_for_model,
    _enforce_context_budget,
    _public_tool_input,
    _run_parallel,
    _summarise,
    _tool_source,
)
from app.cli.support.output import debug_print, get_tracker
from app.constants.investigation import MAX_INVESTIGATION_LOOPS
from app.services.agent_llm_client import ToolCall, get_agent_llm
from app.state.evidence import EvidenceEntry
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.utils.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

# Maps alert_source → tool source keys. Tools from these sources are auto-called
# before the LLM loop starts when the alert source is known.
_ALERT_SOURCE_TO_TOOL_SOURCES: dict[str, list[str]] = {
    "grafana": ["grafana"],
    "datadog": ["datadog"],
    "cloudwatch": ["cloudwatch"],
    "eks": ["eks"],
    "alertmanager": ["grafana", "cloudwatch"],
    "sentry": ["sentry"],
    "honeycomb": ["honeycomb"],
    "coralogix": ["coralogix"],
    "airflow": ["airflow"],
    "hermes": ["hermes"],
    "kafka": ["kafka"],
    "postgresql": ["postgresql"],
    "mysql": ["mysql"],
    "mariadb": ["mariadb"],
    "mongodb": ["mongodb", "mongodb_atlas"],
    "redis": ["redis"],
    "snowflake": ["snowflake"],
    "clickhouse": ["clickhouse"],
    "dagster": ["dagster"],
    "rabbitmq": ["rabbitmq"],
    "supabase": ["supabase"],
    "opensearch": ["opensearch"],
    "openobserve": ["openobserve"],
    "betterstack": ["betterstack"],
    "azure": ["azure", "azure_sql"],
    "splunk": ["splunk"],
    "signoz": ["signoz"],
    "jenkins": ["jenkins"],
    "tempo": ["tempo"],
}

# Consecutive iterations made up ENTIRELY of duplicate (already-seen) tool calls
# that we tolerate before forcing the agent to conclude. Re-running a tool with
# identical arguments returns the same data, so a model that loops on duplicates
# makes no progress yet still burns the whole MAX_INVESTIGATION_LOOPS budget. The
# failure mode is made worse by context trimming (app/agent/tool_loop.py), which
# drops the oldest tool exchanges to fit the window and can erase the very
# history that would otherwise remind the model it already has the result.
_MAX_STAGNANT_ITERATIONS = 2

# Injected as a user turn once the agent starts repeating itself, steering it to
# stop gathering and write the diagnosis from what it already has.
_STAGNATION_NUDGE = (
    "You are repeating tool calls you already made, so they return no new "
    "information and the investigation is not progressing. Stop calling tools and "
    "write your final diagnosis from the evidence already gathered: root cause, "
    "root cause category, supporting evidence, validated and non-validated claims, "
    "remediation steps, and a validity score. If the evidence is insufficient to "
    "determine a root cause, say so explicitly and use a low validity score."
)


def _tool_call_signature(tool_call: ToolCall) -> str:
    """Stable identity for a tool call: ``name`` + canonicalised arguments.

    Two calls to the same tool with the same arguments (regardless of key order)
    produce the same signature. Used to detect when the model re-requests a query
    it has already run so the runtime can refuse to re-execute it.
    """
    try:
        args = json.dumps(tool_call.input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(tool_call.input)
    return f"{tool_call.name}::{args}"


def _duplicate_call_result(tool_call: ToolCall) -> dict[str, Any]:
    """Synthetic result returned in place of re-running an already-seen call.

    Keeps the provider tool_use/tool_result contract valid (every requested call
    still gets a result) while telling the model, in the result itself, that the
    repeat was skipped and what to do instead.
    """
    return {
        "suppressed_duplicate": True,
        "tool": tool_call.name,
        "note": (
            f"Skipped: '{tool_call.name}' was already called earlier in this "
            "investigation with identical arguments, so re-running it would return "
            "the same data. Do not call it again. Either call a DIFFERENT tool (or "
            "the same tool with DIFFERENT arguments) to gather new evidence, or "
            "write your final diagnosis."
        ),
    }


class ConnectedInvestigationAgent:
    """ReAct loop scoped to the tools enabled by connected integrations."""

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — used by overrides
        iteration: int,  # noqa: ARG002 — used by overrides
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools.

        Returns ``(accept_conclusion, nudge)``:
          - ``(True, None)`` — accept the LLM's choice, exit the loop. Default.
          - ``(False, "...")`` — reject the bail, inject the nudge string as a
            user message, continue the loop. ``MAX_INVESTIGATION_LOOPS`` still
            caps the worst case so a stubborn model can't infinite-loop.

        **Contract:** ``(False, None)`` is invalid and raises ``ValueError`` at
        the call site. Rejecting the conclusion without providing a nudge
        would spin the loop on an unchanged message history until the outer
        iteration cap, silently burning the token budget. The type system
        allows ``str | None`` so subclasses can use a single return type;
        the runtime guard enforces the actual contract.

        Default returns ``(True, None)`` — production agents accept whatever
        the LLM decides. Subclasses can override to enforce minimum-evidence
        floors, structured-stage progression, or other termination policies.
        """
        return True, None

    def _filter_tools(
        self,
        tools: list[RegisteredTool],
    ) -> list[RegisteredTool]:
        """Hook: narrow the tool list the agent will see.

        Called once at the start of ``run`` after the registry has produced
        the candidate set for the resolved integrations and before
        ``_build_connected_tool_context`` derives ``state["available_sources"]``
        and ``state["available_action_names"]`` — anything dropped here is
        also dropped from those state fields.

        Default returns the input unchanged. Subclasses can override to
        implement any policy that restricts tool availability per agent
        instance (e.g. enforce an allowlist for an isolated execution mode).
        """
        return tools

    def _build_system_prompt(self, state: dict[str, Any]) -> str:
        """Hook: produce the LLM system prompt for this investigation.

        Called once per ``run`` after the resolved-integrations view has
        been written into ``state``. Default delegates to
        :func:`app.agent.prompt.build_system_prompt` — production behavior
        is unchanged.

        Subclasses can override to swap in a fundamentally different
        instruction shape (e.g. a minimal SRE-diagnostic prompt for a
        pure baseline that needs to NOT inherit opensre's
        planner/verifier instructions). Returning an empty string or
        ``""`` is legal — the LLM will then receive no system prompt at
        all, which is itself a meaningful experimental condition.
        """
        return build_system_prompt(state)

    def run(
        self,
        state: dict[str, Any],
        on_event: AgentEventCallback | None = None,
    ) -> dict[str, Any]:
        """Run the full investigation. Returns a dict of state updates.

        on_event: optional callback invoked with (kind, data) for each
        observable event (tool_start, tool_end, llm_start, agent_end).
        Used by astream_investigation to relay events to the CLI renderer.
        """
        tracker = get_tracker()
        tracker.start("investigation_agent", "Running investigation agent loop")

        def _emit(kind: str, data: dict[str, Any]) -> None:
            if on_event is not None:
                with contextlib.suppress(Exception):
                    on_event(kind, data)

        def _record_tool_start(tc: ToolCall) -> None:
            tracker.record_tool_start(tc.name, redact_sensitive(tc.input), event_key=tc.id)
            _emit("tool_start", _tool_event_payload(tc))

        def _record_tool_end(tc: ToolCall, output: Any) -> None:
            tracker.record_tool_end(
                tc.name,
                redact_sensitive(output),
                event_key=tc.id,
                tool_input=redact_sensitive(tc.input),
            )
            _emit("tool_end", _tool_event_payload(tc, output=output))

        resolved = state.get("resolved_integrations") or {}
        tools = self._filter_tools(_get_available_tools(resolved))
        tool_context = _build_connected_tool_context(resolved, tools)
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
        # Tool-call signatures already executed. Tracked in Python (not in the
        # message history) so it survives context trimming and reliably catches
        # repeats even after the conversation is trimmed to fit the window.
        seen_signatures: set[str] = set()

        _emit(
            "agent_start",
            {
                "tool_count": len(tools),
                "connected_integrations": tool_context["connected_integrations"],
                "available_action_names": tool_context["available_action_names"],
            },
        )

        # Before the LLM loop: deterministically run the primary integration tools
        # based on the alert source. This guarantees the LLM always sees real data
        # from the right integration first, regardless of what it would have chosen.
        seed_calls = _build_seed_calls(state, tools, llm)
        if seed_calls:
            logger.debug("[agent] seeding %d primary tool calls before LLM loop", len(seed_calls))
            for tc in seed_calls:
                seen_signatures.add(_tool_call_signature(tc))
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

            # Inject as a synthetic assistant turn so the LLM sees: user → assistant(tool calls) → tool results
            seed_assistant_msg = _build_synthetic_assistant_tool_call_msg(llm, seed_calls)
            messages.append(seed_assistant_msg)
            messages.extend(seed_msgs)

            for tc, output in zip(seed_calls, seed_results):
                _merge_tool_evidence(evidence, tc.name, output, tc.input)
                evidence_entries.append(
                    EvidenceEntry(
                        key=tc.name,
                        data=redact_sensitive(output),
                        tool_name=tc.name,
                        tool_args=redact_sensitive(tc.input),
                        source=_tool_source(tools, tc.name),
                        loop_iteration=-1,  # -1 = pre-loop seed
                    )
                )
                _record_tool_end(tc, output)
                debug_print(f"[seed:{tc.name}] → {_summarise(output)}")

        # Size the trim ceiling to the ACTIVE model's context window. A flat
        # ceiling overflows smaller-window models (e.g. gpt-4o at 128k) because
        # trimming "down to" an Anthropic-sized ceiling still exceeds their cap.
        context_ceiling = _context_budget_ceiling_for_model(getattr(llm, "_model", None))
        # Consecutive iterations that produced no fresh (non-duplicate) tool call.
        stagnant_iterations = 0
        # Once set, the next invoke offers no tools so the model is forced to emit
        # a textual diagnosis instead of looping on repeats.
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
                updates = _degraded_investigation_from_llm_failure(
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
                return updates

            messages.append(_build_assistant_msg(llm, response))

            if not response.has_tool_calls:
                accept, nudge = self._should_accept_conclusion(
                    evidence_count=len(evidence_entries),
                    iteration=iteration,
                )
                if accept:
                    logger.debug("[agent] no tool calls — done after %d iterations", iteration + 1)
                    break
                # Contract: rejecting the conclusion (accept=False) MUST
                # come with a nudge so the next LLM call sees new context.
                # Without one the loop would spin on an unchanged message
                # history until MAX_INVESTIGATION_LOOPS, silently burning
                # the entire token budget without making progress. Failing
                # fast keeps buggy hook overrides loud rather than expensive.
                if nudge is None:
                    raise ValueError(
                        f"{type(self).__name__}._should_accept_conclusion returned "
                        "(False, None) — a nudge string is required when rejecting "
                        "the conclusion, otherwise the LLM will loop on an unchanged "
                        "message history until MAX_INVESTIGATION_LOOPS."
                    )
                messages.append({"role": "user", "content": nudge})
                continue

            # Split requested calls into fresh ones (not seen before) and repeats.
            # Repeats are NOT re-executed — they get a synthetic "already ran this"
            # result so the provider's tool_use/tool_result contract stays valid
            # while steering the model off the loop.
            duplicate_flags = [
                _tool_call_signature(tc) in seen_signatures for tc in response.tool_calls
            ]
            fresh_calls = [
                tc for tc, is_dup in zip(response.tool_calls, duplicate_flags) if not is_dup
            ]
            for tc in fresh_calls:
                seen_signatures.add(_tool_call_signature(tc))
                _record_tool_start(tc)

            executed_hypotheses.append(
                {
                    "hypothesis": f"Agent iteration {iteration}",
                    "actions": [tc.name for tc in fresh_calls],
                    "loop_iteration": iteration,
                }
            )

            fresh_results = iter(_run_parallel(fresh_calls, tools, resolved) if fresh_calls else [])
            results = [
                _duplicate_call_result(tc) if is_dup else next(fresh_results)
                for tc, is_dup in zip(response.tool_calls, duplicate_flags)
            ]

            tool_result_messages = _build_tool_result_messages(llm, response.tool_calls, results)
            messages.extend(tool_result_messages)

            for tc, output, is_dup in zip(response.tool_calls, results, duplicate_flags):
                if is_dup:
                    debug_print(f"[{tc.name}] → duplicate call suppressed")
                    continue
                _merge_tool_evidence(evidence, tc.name, output, tc.input)
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

            # Stagnation breaker: an iteration with no fresh calls gathered no new
            # evidence. Tolerate a couple (the nudge often unsticks the model) then
            # force a tool-free conclusion rather than spinning to the loop cap.
            if fresh_calls:
                stagnant_iterations = 0
            else:
                stagnant_iterations += 1
                messages.append({"role": "user", "content": _STAGNATION_NUDGE})
                if stagnant_iterations >= _MAX_STAGNANT_ITERATIONS:
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

        result = parse_diagnosis(
            messages,
            evidence,
            state.get("alert_name", ""),
            alert_source=_get_alert_source(state),
        )
        result.evidence = evidence
        result.evidence_entries = [e.model_dump() for e in evidence_entries]
        result.agent_messages = messages

        _emit(
            "agent_end",
            {
                "root_cause": result.root_cause,
                "validity_score": result.validity_score,
                "root_cause_category": result.root_cause_category,
            },
        )

        tracker.complete(
            "investigation_agent",
            fields_updated=["root_cause", "evidence", "validated_claims"],
            message=f"validity:{result.validity_score:.0%} category:{result.root_cause_category}",
        )

        updates = _result_to_state(result)
        updates["executed_hypotheses"] = executed_hypotheses
        updates.update(tool_context)
        return updates


InvestigationAgent = ConnectedInvestigationAgent


def _degraded_investigation_from_llm_failure(
    failure: LLMInvokeFailure,
    *,
    err: BaseException,
    tracker: Any,
    _emit: Callable[[str, dict[str, Any]], None],
    evidence: dict[str, Any],
    evidence_entries: list[EvidenceEntry],
    messages: list[dict[str, Any]],
    executed_hypotheses: list[dict[str, Any]],
    tool_context: dict[str, Any],
) -> dict[str, Any]:
    """Return a partial investigation state when an LLM invoke fails operatively."""
    tracker.error("investigation_agent", message=failure.tracker_message)
    error_msg = f"Error: {failure.user_message}"
    _emit(
        "agent_end",
        {
            "root_cause": error_msg,
            "validity_score": 0.0,
            "root_cause_category": failure.root_cause_category,
        },
    )
    updates = {
        "root_cause": error_msg,
        "root_cause_category": failure.root_cause_category,
        "causal_chain": [f"LLM invoke failed: {err!s}"],
        "validated_claims": [],
        "non_validated_claims": [],
        "remediation_steps": failure.remediation_steps,
        "validity_score": 0.0,
        "investigation_recommendations": [],
        "evidence": evidence,
        "evidence_entries": [e.model_dump() for e in evidence_entries],
        "agent_messages": messages,
        "executed_hypotheses": executed_hypotheses,
    }
    updates.update(tool_context)
    return updates


def _get_available_tools(
    resolved_integrations: dict[str, Any],
) -> list[RegisteredTool]:
    available_sources = _availability_view(resolved_integrations)
    return [t for t in get_registered_tools("investigation") if t.is_available(available_sources)]


def _availability_view(resolved_integrations: dict[str, Any]) -> dict[str, Any]:
    """Adapt resolved integration configs to the legacy tool availability contract.

    Several tools historically used ``connection_verified`` to mean "this
    integration is configured and safe to offer." The current resolver already
    filters out invalid configs, so mark configured integration dicts as
    available for those tools without mutating persisted state.
    """
    view: dict[str, Any] = {}
    for key, value in resolved_integrations.items():
        if key.startswith("_") or not isinstance(value, dict) or not value:
            view[key] = value
            continue
        item = dict(value)
        item.setdefault("connection_verified", True)
        view[key] = item
    return view


def _build_connected_tool_context(
    resolved_integrations: dict[str, Any],
    tools: list[RegisteredTool],
) -> dict[str, Any]:
    from app.integrations.registry import family_key

    connected_integrations = sorted(
        key
        for key, value in resolved_integrations.items()
        if not key.startswith("_") and isinstance(value, dict) and value
    )
    connected_families = {family_key(key) for key in connected_integrations}

    sources: dict[str, dict[str, Any]] = {}
    for tool in sorted(tools, key=lambda item: (str(item.source), item.name)):
        source = str(tool.source)
        source_info = sources.setdefault(
            source,
            {
                "connected": source in connected_integrations
                or family_key(source) in connected_families,
                "tools": [],
            },
        )
        source_info["tools"].append(tool.name)

    return {
        "connected_integrations": connected_integrations,
        "available_sources": sources,
        "available_action_names": [tool.name for tool in sorted(tools, key=lambda item: item.name)],
    }


def _build_seed_calls(
    state: dict[str, Any],
    tools: list[RegisteredTool],
    llm: Any,
) -> list[ToolCall]:
    """Return tool calls to run before the LLM loop based on the alert source.

    Picks all available tools whose source matches the alert's primary integration.
    Returns an empty list when the source is unknown or no matching tools are available.
    """
    alert_source = _get_alert_source(state)
    if not alert_source:
        return []

    target_sources = set(_ALERT_SOURCE_TO_TOOL_SOURCES.get(alert_source, []))
    if not target_sources:
        return []

    resolved = state.get("resolved_integrations") or {}
    seed_tools = [t for t in tools if str(t.source) in target_sources]
    if not seed_tools:
        return []

    from app.services.agent_llm_client import BedrockConverseAgentClient
    from app.services.bedrock_converse import new_tool_use_id

    use_converse_ids = isinstance(llm, BedrockConverseAgentClient)
    calls: list[ToolCall] = []
    for tool in seed_tools:
        try:
            injected = tool.extract_params(resolved)
        except Exception:
            injected = {}
        tool_id = new_tool_use_id() if use_converse_ids else f"seed_{tool.name}"
        calls.append(ToolCall(id=tool_id, name=tool.name, input=_public_tool_input(injected)))

    return calls


def _get_alert_source(state: dict[str, Any]) -> str:
    source = str(state.get("alert_source") or "").lower().strip()
    if source:
        return source
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        source = str(raw.get("alert_source") or "").lower().strip()
        if source:
            return source
        labels = raw.get("commonLabels") or raw.get("labels") or {}
        if isinstance(labels, dict) and (
            labels.get("grafana_folder") or labels.get("datasource_uid")
        ):
            return "grafana"
        ext_url = raw.get("externalURL", "")
        if isinstance(ext_url, str) and "grafana" in ext_url.lower():
            return "grafana"
    return ""


def _tool_event_payload(tc: ToolCall, *, output: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": tc.id,
        "name": tc.name,
        "input": redact_sensitive(tc.input),
    }
    if output is not None:
        payload["output"] = redact_sensitive(output)
    return payload


def _merge_tool_evidence(
    evidence: dict[str, Any],
    tool_name: str,
    output: Any,
    tool_input: dict[str, Any],
) -> None:
    """Store raw tool output and the legacy report-facing evidence keys."""
    evidence[tool_name] = output
    tool_outputs = evidence.setdefault("tool_outputs", [])
    if isinstance(tool_outputs, list):
        tool_outputs.append(
            {
                "tool_name": tool_name,
                "tool_args": redact_sensitive(tool_input),
                "data": redact_sensitive(output),
            }
        )

    if not isinstance(output, dict):
        return

    if tool_name == "query_grafana_logs":
        evidence["grafana_logs"] = output.get("logs", [])
        evidence["grafana_error_logs"] = output.get("error_logs", [])
        evidence["grafana_logs_query"] = output.get("query", "")
        evidence["grafana_logs_service"] = output.get("service_name", "")
        return

    if tool_name == "query_grafana_metrics":
        metric_name = str(output.get("metric_name") or tool_input.get("metric_name") or "")
        metric_results = evidence.setdefault("grafana_metric_results", {})
        if isinstance(metric_results, dict) and metric_name:
            metric_results[metric_name] = output
        evidence["grafana_metrics"] = output.get("metrics", [])
        return

    if tool_name == "query_grafana_traces":
        evidence["grafana_traces"] = output.get("traces", [])
        evidence["grafana_pipeline_spans"] = output.get("pipeline_spans", [])
        return

    if tool_name == "query_grafana_alert_rules":
        evidence["grafana_alert_rules"] = output.get("rules", [])
        return

    if tool_name == "query_grafana_service_names":
        evidence["grafana_service_names"] = output.get("service_names", [])


def _result_to_state(result: InvestigationResult) -> dict[str, Any]:
    return {
        "root_cause": result.root_cause,
        "root_cause_category": result.root_cause_category,
        "causal_chain": result.causal_chain,
        "validated_claims": result.validated_claims,
        "non_validated_claims": result.non_validated_claims,
        "remediation_steps": result.remediation_steps,
        "validity_score": result.validity_score,
        "investigation_recommendations": result.investigation_recommendations,
        "evidence": result.evidence,
        "evidence_entries": result.evidence_entries,
        "agent_messages": result.agent_messages,
    }
