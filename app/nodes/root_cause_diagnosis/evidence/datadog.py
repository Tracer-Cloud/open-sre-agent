"""Datadog evidence section builders for root cause diagnosis prompts."""

from __future__ import annotations

from typing import Any

from app.nodes.root_cause_diagnosis.formatters import _format_datadog_log_entry


def build_datadog_pods_section(evidence: dict[str, Any]) -> str:
    datadog_failed_pods = evidence.get("datadog_failed_pods", [])
    datadog_pod_name = evidence.get("datadog_pod_name")
    datadog_container_name = evidence.get("datadog_container_name")
    datadog_kube_namespace = evidence.get("datadog_kube_namespace")

    if datadog_failed_pods:
        lines = [f"\nFailed Pods ({len(datadog_failed_pods)}):\n"]
        for p in datadog_failed_pods:
            pod_parts = [f"pod={p.get('pod_name', '?')}"]
            if p.get("container"):
                pod_parts.append(f"container={p['container']}")
            if p.get("namespace"):
                pod_parts.append(f"namespace={p['namespace']}")
            if p.get("exit_code") is not None:
                pod_parts.append(f"exit={p['exit_code']}")
            if p.get("node_name"):
                node_str = f"node={p['node_name']}"
                if p.get("node_ip"):
                    node_str += f" ({p['node_ip']})"
                pod_parts.append(node_str)
            if p.get("cluster"):
                pod_parts.append(f"cluster={p['cluster']}")
            if p.get("error"):
                pod_parts.append(f"error={p['error'][:100]}")
            lines.append(f"- {' | '.join(pod_parts)}\n")
        return "".join(lines)

    if datadog_pod_name:
        pod_parts = [f"pod_name={datadog_pod_name}"]
        if datadog_container_name:
            pod_parts.append(f"container={datadog_container_name}")
        if datadog_kube_namespace:
            pod_parts.append(f"namespace={datadog_kube_namespace}")
        return f"\nFailed Pod Location: {' '.join(pod_parts)}\n"

    return ""


def build_datadog_logs_section(evidence: dict[str, Any]) -> str:
    datadog_error_logs = evidence.get("datadog_error_logs", [])
    datadog_logs = evidence.get("datadog_logs", [])

    if datadog_error_logs:
        lines = [f"\nDatadog Error Logs ({len(datadog_error_logs)} events):\n"]
        for log in datadog_error_logs[:15]:
            lines.append(f"- {_format_datadog_log_entry(log)}\n")
        return "".join(lines)

    if datadog_logs:
        lines = [f"\nDatadog Logs ({len(datadog_logs)} events):\n"]
        for log in datadog_logs[:15]:
            lines.append(f"- {_format_datadog_log_entry(log)}\n")
        return "".join(lines)

    return ""


def build_datadog_monitors_section(evidence: dict[str, Any]) -> str:
    datadog_monitors = evidence.get("datadog_monitors", [])
    if not datadog_monitors:
        return ""

    lines = [f"\nDatadog Monitors ({len(datadog_monitors)}):\n"]
    for monitor in datadog_monitors[:5]:
        lines.append(f"- {monitor.get('name', 'unknown')} [{monitor.get('overall_state', '')}]\n")
        lines.append(
            f"  Type: {monitor.get('type', '')}, Query: {monitor.get('query', '')[:200]}\n"
        )
    return "".join(lines)


def build_datadog_events_section(evidence: dict[str, Any]) -> str:
    datadog_events = evidence.get("datadog_events", [])
    if not datadog_events:
        return ""

    lines = [f"\nDatadog Events ({len(datadog_events)}):\n"]
    for event in datadog_events[:5]:
        lines.append(f"- {event.get('title', 'unknown')}\n")
        if event.get("message"):
            lines.append(f"  {event['message'][:200]}\n")
    return "".join(lines)
