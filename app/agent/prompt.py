"""System prompt builders for the investigation agent."""

from __future__ import annotations

from typing import Any

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

## What to produce at the end

When you are done investigating (no more tool calls), write a diagnosis that includes:
- **Root cause**: What failed and why (2-3 sentences, specific)
- **Root cause category**: One of database / infrastructure / code_bug / configuration / network / performance / healthy / unknown
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

# Maps alert_source values to integration source keys (tool `.source` field).
# An alert source can map to multiple integration sources.
_ALERT_SOURCE_TO_TOOL_SOURCES: dict[str, list[str]] = {
    "grafana": ["grafana"],
    "datadog": ["datadog"],
    "cloudwatch": ["cloudwatch", "ec2", "rds"],
    "eks": ["eks", "ec2"],
    "alertmanager": ["eks", "cloudwatch", "grafana"],
    "sentry": ["sentry"],
    "honeycomb": ["honeycomb"],
    "coralogix": ["coralogix"],
    "airflow": ["airflow", "tracer_web"],
    "hermes": ["hermes"],
    "kafka": ["kafka"],
    "postgresql": ["postgresql"],
    "mysql": ["mysql"],
    "mariadb": ["mariadb"],
    "mongodb": ["mongodb", "mongodb_atlas"],
    "snowflake": ["snowflake"],
    "clickhouse": ["clickhouse"],
    "rabbitmq": ["rabbitmq"],
    "supabase": ["supabase"],
    "opensearch": ["opensearch"],
    "openobserve": ["openobserve"],
    "betterstack": ["betterstack"],
    "azure": ["azure", "azure_sql"],
    "github": ["github"],
    "gitlab": ["gitlab"],
    "bitbucket": ["bitbucket"],
    "argocd": ["eks"],
    "splunk": ["splunk"],
}

# Generic fallback sources — always secondary, never primary.
_SECONDARY_SOURCES = {"knowledge", "openclaw", "google_docs"}


def build_system_prompt(_state: dict[str, Any]) -> str:
    return _INVESTIGATION_SYSTEM


def format_alert_context(state: dict[str, Any]) -> str:
    from app.tools.registry import get_registered_tools

    alert_name = state.get("alert_name", "Unknown alert")
    pipeline_name = state.get("pipeline_name", "Unknown pipeline")
    severity = state.get("severity", "unknown")
    alert_source = _get_alert_source(state)

    extra_parts = _build_extra_parts(state)
    extra = ("\n" + "\n".join(extra_parts) + "\n") if extra_parts else ""

    resolved = state.get("resolved_integrations") or {}
    available_tools = [t for t in get_registered_tools("investigation") if t.is_available(resolved)]

    tools_by_source = _group_tools_by_source(available_tools)
    connected_integrations = _format_connected_integrations(
        state.get("available_sources"),
        resolved,
        tools_by_source,
    )
    start_guidance = _build_start_guidance(alert_source, alert_name, tools_by_source)
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
    alert_source: str,
    alert_name: str,
    tools_by_source: dict[str, list[Any]],
) -> str:
    primary_sources = _ALERT_SOURCE_TO_TOOL_SOURCES.get(alert_source, [])
    # Find which primary sources actually have tools available
    available_primary = [s for s in primary_sources if s in tools_by_source]

    if not available_primary:
        # Fall back: any non-secondary source available
        available_primary = [s for s in tools_by_source if s not in _SECONDARY_SOURCES]

    if not available_primary:
        return "No integration-specific tools are available. Use the knowledge tools to reason about this alert."

    lines: list[str] = []
    if alert_source:
        lines.append(f"This is a **{alert_source}** alert ({alert_name}).")
    lines.append(f"Call these tools first (from: {', '.join(available_primary)}):")
    lines.append("")

    for source in available_primary:
        source_tools = tools_by_source.get(source, [])
        tool_names = [f"`{t.name}`" for t in source_tools]
        lines.append(f"- **{source}**: {', '.join(tool_names)}")

    secondary = [
        s for s in tools_by_source if s not in _SECONDARY_SOURCES and s not in available_primary
    ]
    if secondary:
        lines.append("")
        lines.append(
            f"Secondary integrations (use if primary tools return no useful data): {', '.join(secondary)}"
        )

    return "\n".join(lines)


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
        tool_lines = [f"  - `{t.name}`: {t.description}" for t in tools]
        sections.append(f"**{source}**:\n" + "\n".join(tool_lines))

    return "\n\n".join(sections)


def _format_connected_integrations(
    available_sources: Any,
    resolved_integrations: dict[str, Any],
    tools_by_source: dict[str, list[Any]],
) -> str:
    connected = sorted(
        key
        for key, value in resolved_integrations.items()
        if not key.startswith("_") and isinstance(value, dict) and value
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
