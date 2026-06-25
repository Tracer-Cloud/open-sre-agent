"""Tool-call labeling and payload extraction helpers."""

from __future__ import annotations

from typing import Any

from app.tools.registry import get_registered_tool_map, resolve_tool_display_name


def _tool_event_key(data: dict[str, Any], name: str) -> str:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return str(
        data.get("id")
        or data.get("tool_call_id")
        or nested.get("id")
        or nested.get("tool_call_id")
        or name
    )


def _tool_source_label(tool_name: str) -> str:
    tool = get_registered_tool_map().get(tool_name)
    source = str(tool.source) if tool is not None else _infer_tool_source(tool_name)
    if source == "grafana":
        return "Grafana"
    if source == "knowledge":
        return "SRE"
    if source == "openclaw":
        return "OpenClaw"
    return source.replace("_", " ").title() if source else "Tools"


def _infer_tool_source(tool_name: str) -> str:
    lowered = tool_name.lower()
    for source in ("grafana", "datadog", "cloudwatch", "sentry", "honeycomb", "openclaw"):
        if source in lowered:
            return source
    if lowered.startswith("get_sre_"):
        return "knowledge"
    return "tools"


def _tool_short_label(tool_name: str, source_label: str) -> str:
    display = resolve_tool_display_name(tool_name)
    label = display
    for prefix in (
        source_label,
        source_label.lower(),
        f"{source_label} ",
        f"{source_label.lower()} ",
        "query ",
        "get ",
    ):
        if label.startswith(prefix):
            label = label[len(prefix) :].strip()
    if source_label == "Grafana" and label.lower().startswith("grafana "):
        label = label[len("grafana ") :].strip()
    return label or display


def _tool_input(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("input", nested.get("input", {}))


def _tool_output(data: dict[str, Any]) -> Any:
    nested = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    return data.get("output", nested.get("output", {}))
