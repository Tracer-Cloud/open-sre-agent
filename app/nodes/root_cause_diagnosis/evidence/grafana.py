"""Grafana evidence section builders for root cause diagnosis prompts."""

from __future__ import annotations

import json
from typing import Any

from app.nodes.root_cause_diagnosis.formatters import _format_grafana_log_entry


def build_grafana_logs_section(evidence: dict[str, Any]) -> str:
    grafana_error_logs = evidence.get("grafana_error_logs", [])
    grafana_logs = evidence.get("grafana_logs", [])

    if grafana_error_logs:
        lines = [f"\nGrafana Error Logs ({len(grafana_error_logs)} events):\n"]
        for log in grafana_error_logs[:10]:
            lines.append(f"- {_format_grafana_log_entry(log)}\n")
        return "".join(lines)

    if grafana_logs:
        lines = [f"\nGrafana Logs ({len(grafana_logs)} events):\n"]
        for log in grafana_logs[:10]:
            lines.append(f"- {_format_grafana_log_entry(log)}\n")
        return "".join(lines)

    return ""


def build_grafana_spans_section(evidence: dict[str, Any]) -> str:
    grafana_spans = evidence.get("grafana_pipeline_spans", [])
    if not grafana_spans:
        return ""

    lines = [f"\nGrafana Pipeline Spans ({len(grafana_spans)}):\n"]
    for span in grafana_spans[:10]:
        run_id = span.get("execution_run_id", "")
        records = span.get("record_count", "")
        entry = f"- {span.get('span_name', 'unknown')}"
        if run_id:
            entry += f" (run_id={run_id})"
        if records:
            entry += f" records={records}"
        lines.append(entry + "\n")
    return "".join(lines)


def build_grafana_metrics_section(evidence: dict[str, Any]) -> str:
    grafana_metric_summaries = evidence.get("grafana_metric_summaries", [])
    grafana_metrics = evidence.get("grafana_metrics", [])

    if grafana_metric_summaries:
        metric_name = evidence.get("grafana_metric_name", "unknown")
        lines = [f"\nGrafana Metrics ({metric_name}):\n"]
        for metric in grafana_metric_summaries[:8]:
            if not isinstance(metric, dict):
                continue
            summary = metric.get("summary")
            if summary:
                lines.append(f"- {summary}\n")
        return "".join(lines)

    if grafana_metrics:
        metric_name = evidence.get("grafana_metric_name", "unknown")
        lines = [f"\nGrafana Metrics ({metric_name}):\n"]
        for metric in grafana_metrics[:5]:
            lines.append(f"- {json.dumps(metric, default=str)[:200]}\n")
        return "".join(lines)

    return ""


def build_grafana_alert_rules_section(evidence: dict[str, Any]) -> str:
    grafana_alert_rules = evidence.get("grafana_alert_rules", [])
    if not grafana_alert_rules:
        return ""

    lines = [f"\nGrafana Alert Rules ({len(grafana_alert_rules)}):\n"]
    for rule in grafana_alert_rules[:5]:
        lines.append(f"- {rule.get('rule_name', 'unknown')} [{rule.get('state', '')}]\n")
        lines.append(f"  Folder: {rule.get('folder', '')}, Group: {rule.get('group', '')}\n")
        for query in rule.get("queries", [])[:2]:
            lines.append(f"  Query ({query.get('ref_id', '')}): {query.get('expr', '')[:200]}\n")
        if rule.get("no_data_state"):
            lines.append(f"  No-data state: {rule.get('no_data_state')}\n")
    return "".join(lines)
