"""Tool source/label formatting shared by ``surfaces/cli`` and ``surfaces/interactive_shell``.

Both surfaces render the same live tool-call activity (a source badge like
``Grafana`` or ``SRE``, and a short human label with the source prefix
stripped) while an investigation runs. The two implementations were
identical, so this is the T-21 extraction: pure formatting with no
surface-specific state, safe to share.
"""

from __future__ import annotations

from tools.registry import get_registered_tool_map, resolve_tool_display_name


def tool_source_label(tool_name: str) -> str:
    """Return a human-friendly source badge (e.g. ``Grafana``, ``SRE``) for a tool."""
    tool = get_registered_tool_map().get(tool_name)
    source = str(tool.source) if tool is not None else infer_tool_source(tool_name)
    if source == "grafana":
        return "Grafana"
    if source == "knowledge":
        return "SRE"
    if source == "openclaw":
        return "OpenClaw"
    return source.replace("_", " ").title() if source else "Tools"


def infer_tool_source(tool_name: str) -> str:
    """Guess a tool's source from its name when the registry has no entry for it."""
    lowered = tool_name.lower()
    for source in ("grafana", "datadog", "cloudwatch", "sentry", "honeycomb", "openclaw"):
        if source in lowered:
            return source
    if lowered.startswith("get_sre_"):
        return "knowledge"
    return "tools"


def tool_short_label(tool_name: str, source_label: str) -> str:
    """Return the tool's display name with its source prefix (if any) stripped."""
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
