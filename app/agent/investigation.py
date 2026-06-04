"""ReAct investigation agent — the core think → call tools → observe loop."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

from app.agent.llm_invoke_errors import LLMInvokeFailure, classify_llm_invoke_failure
from app.agent.prompt import build_system_prompt, format_alert_context
from app.agent.result import InvestigationResult, parse_diagnosis
from app.cli.support.output import debug_print, get_tracker
from app.constants.investigation import MAX_INVESTIGATION_LOOPS
from app.services.agent_llm_client import ToolCall, get_agent_llm
from app.state.evidence import EvidenceEntry
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.utils.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

_TOOL_EXECUTOR_WORKERS = 10
_UNSET: object = object()  # sentinel distinguishing "not yet started" from a None tool result

# Defensive context-window ceiling. Below this we never trim; above this we
# drop the oldest tool_use/tool_result pair until back under the ceiling.
#
# Anthropic's 200k prompt limit is the hard cap. The estimator at
# ``_estimate_message_tokens`` covers messages + system + tool schemas
# (all three count toward the limit). 170k ceiling leaves ~30k headroom
# for the response. ratio=0.40 absorbs JSON-structural overhead in tool
# payloads — empirically tuned from overflow logs where Anthropic landed
# at 0.32–0.40 tokens/char for opensre's tool-result mix.
_TOKEN_BUDGET_CEILING = 170_000
_TOKENS_PER_CHAR = 0.40

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
}

# Callback type: called with (event_kind, data_dict) during the agent loop.
# event_kind values: "tool_start", "tool_end", "llm_start", "agent_start", "agent_end"
AgentEventCallback = Callable[[str, dict[str, Any]], None]


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
        tools = _get_available_tools(resolved)
        tool_context = _build_connected_tool_context(resolved, tools)
        state["available_sources"] = tool_context["available_sources"]
        state["available_action_names"] = tool_context["available_action_names"]

        if not tools:
            logger.warning("No tools available for investigation")

        llm = get_agent_llm()
        tool_schemas = llm.tool_schemas(tools)

        system = build_system_prompt(state)
        alert_text = format_alert_context(state)
        messages: list[dict[str, Any]] = [{"role": "user", "content": alert_text}]

        evidence: dict[str, Any] = {}
        evidence_entries: list[EvidenceEntry] = []
        executed_hypotheses: list[dict[str, Any]] = []

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

        for iteration in range(MAX_INVESTIGATION_LOOPS):
            logger.debug("[agent] iteration=%d", iteration)
            _emit("llm_start", {"iteration": iteration})
            _enforce_context_budget(messages, system=system, tools=tool_schemas)
            try:
                response = llm.invoke(messages, system=system, tools=tool_schemas)

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

            # Emit tool_start for each pending call before executing
            for tc in response.tool_calls:
                _record_tool_start(tc)
            executed_hypotheses.append(
                {
                    "hypothesis": f"Agent iteration {iteration}",
                    "actions": [tc.name for tc in response.tool_calls],
                    "loop_iteration": iteration,
                }
            )

            results = _run_parallel(response.tool_calls, tools, resolved)

            tool_result_messages = _build_tool_result_messages(llm, response.tool_calls, results)
            messages.extend(tool_result_messages)

            for tc, output in zip(response.tool_calls, results):
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


def _estimate_message_tokens(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Cheap upper-bound token estimate covering everything Anthropic sees.

    Anthropic counts ``messages`` + ``system`` + ``tools`` toward the 200k
    prompt limit. Earlier versions counted only ``messages`` and trimmed
    aggressively while system + tools (tens of thousands of tokens for
    opensre's 100+ tool registry) silently pushed us over the line.
    """
    total = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total += int(len(content) * _TOKENS_PER_CHAR)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += int(len(json.dumps(block, default=str)) * _TOKENS_PER_CHAR)
                elif isinstance(block, str):
                    total += int(len(block) * _TOKENS_PER_CHAR)
    if system:
        total += int(len(system) * _TOKENS_PER_CHAR)
    if tools:
        for schema in tools:
            total += int(len(json.dumps(schema, default=str)) * _TOKENS_PER_CHAR)
    return total


def _trim_oldest_tool_pair(messages: list[dict[str, Any]]) -> bool:
    """Drop the oldest assistant tool_use message together with the
    immediate next user message carrying its tool_results. Anthropic
    requires every ``tool_use`` block to be followed by a matching
    ``tool_result`` block, so the pair must be removed together to keep
    the conversation valid.

    Returns True if a pair was dropped, False if nothing trimmable
    remains (e.g. only the initial user prompt is left).
    """
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        has_tool_use = any(
            isinstance(block, dict) and block.get("type") == "tool_use" for block in content
        )
        if not has_tool_use:
            continue
        # Drop the assistant turn AND its paired user turn (the tool_results).
        # If the user turn isn't present (e.g. truncated mid-iteration),
        # del messages[i:i+2] safely just drops the assistant turn.
        del messages[index : index + 2]
        return True
    return False


def _enforce_context_budget(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    """Trim oldest tool pairs until prompt fits under the budget ceiling.

    No-op on the happy path: the estimate covers messages + system + tools
    in one pass and returns under the ceiling for normal investigations.
    Only fires on long investigations where unbounded tool history has
    pushed the prompt past the model's limit.
    """
    while _estimate_message_tokens(messages, system=system, tools=tools) > _TOKEN_BUDGET_CEILING:
        if not _trim_oldest_tool_pair(messages):
            return
        logger.warning("[agent] trimmed oldest tool pair to fit context budget")


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


def _build_synthetic_assistant_tool_call_msg(
    llm: Any,
    tool_calls: list[ToolCall],
) -> dict[str, Any]:
    """Build an assistant message that looks like the LLM requested these tool calls.

    This lets us inject pre-seeded tool results into the conversation in a format
    the LLM client already understands, without adding special-case handling.
    """
    from app.services.agent_llm_client import (
        AnthropicAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    if isinstance(llm, BedrockConverseAgentClient):
        from app.services.bedrock_converse import build_assistant_tool_use_message

        return build_assistant_tool_use_message(tool_calls)

    if isinstance(llm, AnthropicAgentClient):
        content = [
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in tool_calls
        ]
        return {"role": "assistant", "content": content}

    if isinstance(llm, OpenAIAgentClient):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ],
        }

    if isinstance(llm, CLIBackedAgentClient):
        return llm.build_assistant_message("", tool_calls)

    # Fallback: plain text summary
    names = ", ".join(tc.name for tc in tool_calls)
    return {"role": "assistant", "content": f"I will start by querying: {names}"}


def _run_parallel(
    tool_calls: list[ToolCall],
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
) -> list[Any]:
    tool_map = {t.name: t for t in tools}

    def _call(tc: ToolCall) -> Any:
        tool = tool_map.get(tc.name)
        if tool is None:
            return {"error": f"unknown tool: {tc.name}"}
        try:
            validation_error = tool.validate_public_input(tc.input)
            if validation_error:
                return {"error": validation_error}
            injected = tool.extract_params(resolved_integrations)
            kwargs = {**injected, **tc.input}
            return tool.run(**kwargs)
        except Exception as exc:
            logger.warning("[tool:%s] failed: %s", tc.name, exc)
            return {"error": str(exc)}

    if len(tool_calls) == 1:
        return [_call(tool_calls[0])]

    results: list[Any] = [_UNSET] * len(tool_calls)
    submitted: dict[
        Future[Any], int
    ] = {}  # future -> index, built incrementally to survive partial submit
    try:
        with ThreadPoolExecutor(max_workers=min(_TOOL_EXECUTOR_WORKERS, len(tool_calls))) as pool:
            for i, tc in enumerate(tool_calls):
                submitted[pool.submit(_call, tc)] = i
            for fut in as_completed(submitted):
                try:
                    results[submitted[fut]] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[submitted[fut]] = {"error": str(fut_exc)}
    except RuntimeError as exc:
        # interpreter is shutting down; executor.__exit__ has already waited for submitted futures
        logger.warning("[_run_parallel] RuntimeError – falling back to sequential: %s", exc)
        for fut, i in submitted.items():
            if results[i] is _UNSET and fut.done():
                try:
                    results[i] = fut.result()
                except Exception as fut_exc:  # noqa: BLE001  # lgtm[py/catch-base-exception]
                    results[i] = {"error": str(fut_exc)}
        for i, tc in enumerate(tool_calls):
            if results[i] is _UNSET:
                results[i] = _call(tc)
    return results


def _public_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive(value)
    return {
        key: item
        for key, item in redacted.items()
        if item != "[runtime object]" and item != "[redacted]"
    }


def _tool_event_payload(tc: ToolCall, *, output: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": tc.id,
        "name": tc.name,
        "input": redact_sensitive(tc.input),
    }
    if output is not None:
        payload["output"] = redact_sensitive(output)
    return payload


def _tool_source(tools: list[RegisteredTool], tool_name: str) -> str:
    for tool in tools:
        if tool.name == tool_name:
            return str(tool.source)
    return "unknown"


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


def _build_assistant_msg(llm: Any, response: Any) -> dict[str, Any]:
    from app.services.agent_llm_client import AnthropicAgentClient, BedrockConverseAgentClient

    if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
        return llm.build_assistant_message(response.raw_content)
    # Use raw_content when set — preserves provider-specific fields such as
    # Gemini's thought_signature that must be echoed back in the next request.
    if response.raw_content is not None:
        return response.raw_content  # type: ignore[no-any-return]
    result: dict[str, Any] = llm.build_assistant_message(response.content, response.tool_calls)
    return result


def _build_tool_result_messages(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
) -> list[dict[str, Any]]:
    from app.services.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    if isinstance(llm, AnthropicAgentClient):
        return [llm.build_tool_result_message(tool_calls, results)]
    if isinstance(llm, OpenAIAgentClient):
        return llm.build_tool_result_messages(tool_calls, results)
    return [llm.build_tool_result_message(tool_calls, results)]


def _summarise(output: Any) -> str:
    if isinstance(output, dict) and "error" in output:
        return f"error: {output['error']}"
    text = json.dumps(output, default=str)
    return text[:120] + "…" if len(text) > 120 else text


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
