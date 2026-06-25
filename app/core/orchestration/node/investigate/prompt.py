"""System prompt builders for the investigation agent."""

from __future__ import annotations

from typing import Any

from app.core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_TOOL_SOURCES,
    SECONDARY_TOOL_SOURCES,
    relevant_sources_for_alert,
    resolve_alert_source,
)
from app.core.domain.types.root_cause_categories import (
    HERMES_ROOT_CAUSE_CATEGORIES,
    render_prompt_taxonomy,
)

_INVESTIGATION_SYSTEM = """You are Tracer, an AI SRE performing a live production incident investigation.

Your task: investigate the alert below and produce a clear, evidence-backed root cause analysis.

## How to work

1. **Start with the primary integration tools listed under "Where to start".** Those tools directly match the alert source — call them first, in parallel where possible.
2. After each round of results, reason about what you found and decide what to investigate next.
3. Exhaust the primary integration before branching to secondary ones.
4. When you have enough evidence (or all relevant tools are exhausted), write your final diagnosis.

## Rules

- Never guess when a tool can answer — use it.
- Report what tools actually returned. Do not invent log lines or metrics.
- If a tool returns an error or empty result, try another tool from the same integration before giving up.
- If all evidence points to healthy service, say so clearly (root_cause_category = healthy).
- Be specific: include error messages, timestamps, service names, namespaces, run IDs.
- **Only call tools listed under "Available tools".** Do not fabricate tool calls for integrations not listed.
- **Never call the same tool with the same arguments twice.** You already have that result — re-running it returns nothing new and wastes the investigation. Re-running is only useful with *different* arguments (e.g. a different service, time window, or query).
- **Discovery or listing tools (those that just enumerate other tools or resources) are useful at most once.** Call such a tool a single time, then act on what it returned — do not keep re-listing.
- **Prefer tools relevant to the alert.** Do not fan out to integrations unrelated to the alert's service or symptoms just because they are available.
- **If your recent tool calls stopped producing new evidence, stop investigating and write your diagnosis** with whatever you have, rather than repeating calls.
- **Dependency traversal (connection failures only):** When logs show connection-related errors (connection refused, timeout, authentication failure, write failure, port unreachable), the fault may live in a stateful dependency (database, cache, message queue) rather than the caller. Before concluding, also query error logs on the dependency itself (MySQL, Postgres, Redis, RabbitMQ, etc.) using the log tools listed under Available tools. Dependencies log their own failure modes (read-only mode, pool exhaustion, replication errors, slow queries, credential rejections) that are not visible from the caller's side. When multiple pods in a namespace fail together, also check namespace-level resources (quotas, network policies, service accounts). This expands evidence collection only — it does not bias localization; if the dependency is healthy and the caller's config is wrong, the caller is the fault.

## What to produce at the end

When you are done investigating (no more tool calls), write a diagnosis that includes:
- **Root cause**: What failed and why (2-3 sentences, specific)
- **Root cause category**: {root_cause_category_instruction}
- **Evidence**: Which tool results support your conclusion
- **Validated claims**: Specific facts confirmed by evidence (e.g. "Error rate spiked to 47% at 14:32 UTC per Grafana logs")
- **Non-validated claims**: Hypotheses you could not confirm
- **Remediation steps**: Ordered, concrete actions to fix the issue
- **Validity score**: 0.0–1.0 reflecting your confidence based on evidence quality
"""

_ALERT_CONTEXT_TEMPLATE = """## Alert

Alert name: {alert_name}
Alert source: {alert_source}
Service or pipeline: {pipeline_name}
Severity: {severity}
{extra}
## Connected integrations

{connected_integrations}

## Where to start

{start_guidance}

## Available tools (by integration)

{tools_by_source}
"""

_ALERT_SOURCE_TO_TOOL_SOURCES = {
    source: list(tool_sources) for source, tool_sources in ALERT_SOURCE_TO_TOOL_SOURCES.items()
}
_SECONDARY_SOURCES = SECONDARY_TOOL_SOURCES

_DEFAULT_ROOT_CAUSE_CATEGORY_INSTRUCTION = (
    "One of database / infrastructure / code_bug / configuration / network / performance / "
    "healthy / unknown"
)


def build_system_prompt(state: dict[str, Any]) -> str:
    alert_source = resolve_alert_source(state)
    root_cause_category_instruction = _DEFAULT_ROOT_CAUSE_CATEGORY_INSTRUCTION

    if alert_source == "hermes":
        taxonomy = render_prompt_taxonomy(
            HERMES_ROOT_CAUSE_CATEGORIES | {"healthy", "unknown"}
        ).strip()
        root_cause_category_instruction = (
            "Use exactly one category name from the Hermes taxonomy below\n\n"
            "## Hermes root cause category taxonomy (single source of truth)\n"
            f"{taxonomy}"
        )

    return _INVESTIGATION_SYSTEM.format(
        root_cause_category_instruction=root_cause_category_instruction
    )


def format_alert_context(state: dict[str, Any]) -> str:
    from app.tools.registry import get_registered_tools

    alert_name = state.get("alert_name", "Unknown alert")
    pipeline_name = state.get("pipeline_name", "Unknown pipeline")
    severity = state.get("severity", "unknown")
    alert_source = resolve_alert_source(state)

    extra_parts = _build_extra_parts(state)
    extra = ("\n" + "\n".join(extra_parts) + "\n") if extra_parts else ""

    resolved = state.get("resolved_integrations") or {}
    available_tools = [t for t in get_registered_tools("investigation") if t.is_available(resolved)]
    available_tools = _tools_for_plan(available_tools, state)

    tools_by_source = _group_tools_by_source(available_tools)
    connected_integrations = _format_connected_integrations(
        state.get("available_sources"),
        resolved,
        tools_by_source,
    )
    start_guidance = _build_start_guidance(state, alert_source, alert_name, tools_by_source)
    tools_section = _format_tools_by_source(tools_by_source)

    return _ALERT_CONTEXT_TEMPLATE.format(
        alert_name=alert_name,
        alert_source=alert_source or "unknown",
        pipeline_name=pipeline_name,
        severity=severity,
        extra=extra,
        connected_integrations=connected_integrations,
        start_guidance=start_guidance,
        tools_by_source=tools_section,
    )


def _build_extra_parts(state: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    raw_alert = state.get("raw_alert")
    if isinstance(raw_alert, dict):
        if raw_alert.get("error_message"):
            parts.append(f"Error: {raw_alert['error_message']}")
        if raw_alert.get("kube_namespace"):
            parts.append(f"Namespace: {raw_alert['kube_namespace']}")
        labels = raw_alert.get("commonLabels") or raw_alert.get("labels") or {}
        if isinstance(labels, dict):
            if labels.get("datasource_uid"):
                parts.append(f"Datasource UID: {labels['datasource_uid']}")
            if labels.get("grafana_folder"):
                parts.append(f"Grafana folder: {labels['grafana_folder']}")
            if labels.get("rulename"):
                parts.append(f"Alert rule: {labels['rulename']}")
        annotations = raw_alert.get("commonAnnotations") or {}
        if isinstance(annotations, dict) and annotations.get("description"):
            parts.append(f"Description: {annotations['description']}")
    elif isinstance(raw_alert, str) and raw_alert.strip():
        parts.append(f"Raw alert:\n{raw_alert[:2000]}")

    problem_md = state.get("problem_md")
    if problem_md and isinstance(problem_md, str):
        parts.append(problem_md)

    incident_window = state.get("incident_window")
    if isinstance(incident_window, dict):
        start = incident_window.get("start", "")
        end = incident_window.get("end", "")
        if start and end:
            parts.append(f"Incident window: {start} → {end}")

    return parts


def _group_tools_by_source(tools: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for tool in tools:
        source = str(tool.source)
        grouped.setdefault(source, []).append(tool)
    return grouped


def _build_start_guidance(
    state: dict[str, Any],
    alert_source: str,
    alert_name: str,
    tools_by_source: dict[str, list[Any]],
) -> str:
    planned_actions = _planned_action_names(state)
    if planned_actions:
        rationale = str(state.get("plan_rationale") or "").strip()
        planned_list = ", ".join(f"`{name}`" for name in planned_actions)
        lines = ["Use the planned investigation actions first:", "", f"- {planned_list}"]
        if rationale:
            lines.extend(["", f"Plan rationale: {rationale}"])
        return "\n".join(lines)

    primary_sources = _ALERT_SOURCE_TO_TOOL_SOURCES.get(alert_source, [])
    available_primary = [s for s in primary_sources if s in tools_by_source]

    non_secondary = [s for s in tools_by_source if s not in _SECONDARY_SOURCES]
    if not available_primary and not non_secondary:
        return "No integration-specific tools are available. Use the knowledge tools to reason about this alert."

    # Known alert source that maps to connected integrations: start there.
    if available_primary:
        return _format_call_first(alert_source, alert_name, available_primary, tools_by_source)

    # Unknown/generic alert source: pick integrations by alert *content* instead
    # of fanning out to every connected integration. Only when nothing matches
    # do we hand the choice to the LLM — and even then we never instruct it to
    # call them all.
    relevant = _relevant_sources(state, tools_by_source)
    if relevant:
        return _format_call_first(alert_source, alert_name, relevant, tools_by_source)

    available_list = ", ".join(sorted(non_secondary))
    return (
        f"The alert source is not tied to a specific integration ({alert_name}).\n"
        f"Connected integrations available: {available_list}.\n"
        "Review the alert details above and call only the integration(s) directly "
        "relevant to this alert's service or symptoms. Do not call integrations "
        "that are unrelated to the alert."
    )


def _format_call_first(
    alert_source: str,
    alert_name: str,
    call_first: list[str],
    tools_by_source: dict[str, list[Any]],
) -> str:
    lines: list[str] = []
    if alert_source:
        lines.append(f"This is a **{alert_source}** alert ({alert_name}).")
    lines.append(f"Call these tools first (from: {', '.join(call_first)}):")
    lines.append("")

    for source in call_first:
        source_tools = tools_by_source.get(source, [])
        tool_names = [f"`{t.name}`" for t in source_tools]
        lines.append(f"- **{source}**: {', '.join(tool_names)}")

    secondary = [s for s in tools_by_source if s not in _SECONDARY_SOURCES and s not in call_first]
    if secondary:
        lines.append("")
        lines.append(
            f"Secondary integrations (use if primary tools return no useful data): {', '.join(secondary)}"
        )

    return "\n".join(lines)


def _tools_for_plan(tools: list[Any], state: dict[str, Any]) -> list[Any]:
    planned_actions = _planned_action_names(state)
    if not planned_actions:
        return tools
    by_name = {str(tool.name): tool for tool in tools}
    planned_tools = [by_name[name] for name in planned_actions if name in by_name]
    return planned_tools or tools


def _planned_action_names(state: dict[str, Any]) -> list[str]:
    planned_raw = state.get("planned_actions")
    if not isinstance(planned_raw, list):
        return []
    return [str(name).strip() for name in planned_raw if str(name).strip()]


def _relevant_sources(
    state: dict[str, Any],
    tools_by_source: dict[str, list[Any]],
) -> list[str]:
    """Select integration sources relevant to the alert's content.

    Honors an explicit ``context_sources`` annotation when present, otherwise
    keyword-matches the alert text against each available source. Returns an
    empty list when nothing is clearly relevant (the caller then defers the
    choice to the LLM rather than calling every integration).
    """
    return relevant_sources_for_alert(state, tools_by_source.keys())


def _format_tools_by_source(tools_by_source: dict[str, list[Any]]) -> str:
    if not tools_by_source:
        return "No tools available."

    sections: list[str] = []
    # Primary/non-secondary first, then secondary
    ordered_sources = sorted(
        tools_by_source.keys(),
        key=lambda s: (s in _SECONDARY_SOURCES, s),
    )
    for source in ordered_sources:
        tools = tools_by_source[source]
        tool_lines = []
        for tool in tools:
            details: list[str] = []
            if getattr(tool, "source_id", None):
                details.append(f"source_id={tool.source_id}")
            if getattr(tool, "evidence_type", None):
                details.append(f"evidence={tool.evidence_type}")
            if getattr(tool, "side_effect_level", None):
                details.append(f"side_effect={tool.side_effect_level}")
            examples = getattr(tool, "examples", None) or []
            anti_examples = getattr(tool, "anti_examples", None) or []
            output_schema = getattr(tool, "output_schema", None)
            if isinstance(output_schema, dict):
                output_props = output_schema.get("properties")
                if isinstance(output_props, dict) and output_props:
                    output_keys = ", ".join(sorted(str(key) for key in output_props)[:6])
                    details.append(f"outputs={output_keys}")
            if examples:
                details.append(f"example={examples[0]}")
            if anti_examples:
                details.append(f"avoid={anti_examples[0]}")

            suffix = f" ({'; '.join(details)})" if details else ""
            tool_lines.append(f"  - `{tool.name}`: {tool.description}{suffix}")
        sections.append(f"**{source}**:\n" + "\n".join(tool_lines))

    return "\n\n".join(sections)


def _format_connected_integrations(
    available_sources: Any,
    resolved_integrations: dict[str, Any],
    tools_by_source: dict[str, list[Any]],
) -> str:
    from pydantic import BaseModel

    connected = sorted(
        key
        for key, value in resolved_integrations.items()
        if not key.startswith("_")
        and (isinstance(value, BaseModel) or (isinstance(value, dict) and value))
    )
    if not connected and not tools_by_source:
        return "No connected integrations were found."

    if isinstance(available_sources, dict) and available_sources:
        lines: list[str] = []
        for source in sorted(available_sources):
            info = available_sources[source]
            if not isinstance(info, dict):
                continue
            tools = info.get("tools") or []
            tool_names = ", ".join(f"`{name}`" for name in tools) if tools else "no tools"
            status = "connected" if info.get("connected") else "available"
            lines.append(f"- **{source}** ({status}): {tool_names}")
        if lines:
            return "\n".join(lines)

    lines = []
    for source in connected:
        source_tools = tools_by_source.get(source, [])
        tool_names = (
            ", ".join(f"`{tool.name}`" for tool in source_tools) if source_tools else "no tools"
        )
        lines.append(f"- **{source}** (connected): {tool_names}")
    for source in sorted(set(tools_by_source) - set(connected)):
        tool_names = ", ".join(f"`{tool.name}`" for tool in tools_by_source[source])
        lines.append(f"- **{source}** (available): {tool_names}")
    return "\n".join(lines) if lines else "No connected integrations exposed tools."
