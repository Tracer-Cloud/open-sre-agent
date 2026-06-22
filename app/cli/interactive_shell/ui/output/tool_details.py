from __future__ import annotations

from typing import Any

from rich.text import Text

from app.cli.interactive_shell.ui.time_format import _elapsed_hms, _fmt_timing
from app.cli.interactive_shell.ui.theme import BRAND, DIM, HIGHLIGHT, SECONDARY, TEXT
from app.tools.registry import get_registered_tool_map, resolve_tool_display_name
from app.utils.tool_trace import format_json_preview


def tool_source_label(tool_name: str) -> str:
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
    lowered = tool_name.lower()
    for source in ("grafana", "datadog", "cloudwatch", "sentry", "honeycomb", "openclaw"):
        if source in lowered:
            return source
    if lowered.startswith("get_sre_"):
        return "knowledge"
    return "tools"


def tool_short_label(tool_name: str, source_label: str) -> str:
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


def record_tool_summary(
    tool_name: str,
    summary_counts: dict[str, dict[str, int]],
    summary_order: list[tuple[str, str]],
) -> None:
    source = tool_source_label(tool_name)
    label = tool_short_label(tool_name, source)
    source_counts = summary_counts.setdefault(source, {})
    if label not in source_counts:
        summary_order.append((source, label))
    source_counts[label] = source_counts.get(label, 0) + 1


def format_tool_summary(
    summary_counts: dict[str, dict[str, int]],
    summary_order: list[tuple[str, str]],
) -> str:
    source_labels: dict[str, list[str]] = {}
    for source, label in summary_order:
        count = summary_counts.get(source, {}).get(label, 0)
        if count <= 0:
            continue
        rendered = f"{label} x{count}" if count > 1 else label
        source_labels.setdefault(source, []).append(rendered)
    parts = [
        f"{source}: {', '.join(labels[:4])}{', ...' if len(labels) > 4 else ''}"
        for source, labels in source_labels.items()
    ]
    summary = " | ".join(parts[:2])
    return summary[:117] + "..." if len(summary) > 120 else summary


def build_tool_call_line(tool_name: str, elapsed_ms: int, elapsed_total: float) -> Text:
    source = tool_source_label(tool_name)
    label = tool_short_label(tool_name, source)
    call_display = f"{source} · {label}" if label else source
    t = Text()
    t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
    t.append("      ↳  ", style=DIM)
    t.append(call_display, style=BRAND)
    t.append(f"  {_fmt_timing(elapsed_ms)}", style=SECONDARY)
    return t


def make_tool_detail_record(
    display: str,
    tool_input: Any,
    output: Any,
    *,
    elapsed: str = "",
) -> dict[str, Any] | None:
    if tool_input in ({}, None) and output in ({}, None, ""):
        return None
    return {"display": display, "input": tool_input, "output": output, "elapsed": elapsed}


def build_tool_detail_text(record: dict[str, Any]) -> Text:
    display = str(record.get("display") or "tool")
    elapsed = str(record.get("elapsed") or "")
    suffix = f"  {elapsed}" if elapsed else ""
    detail = Text()
    detail.append(f"  Tool details: {display}{suffix}\n", style=f"bold {TEXT}")
    for line in tool_detail_body(record).splitlines():
        detail.append(f"    {line}\n", style=DIM)
    return detail


def tool_detail_body(record: dict[str, Any]) -> str:
    body_parts: list[str] = []
    if (tool_input := record.get("input")) not in ({}, None):
        body_parts.append(f"Input:\n{format_json_preview(tool_input, max_chars=1600)}")
    if (output := record.get("output")) not in ({}, None, ""):
        body_parts.append(f"Output:\n{format_json_preview(output, max_chars=3000)}")
    return "\n\n".join(body_parts)


def build_live_tool_detail_rows(
    records: list[dict[str, Any]], max_width: int, now: float
) -> list[Text]:
    rows: list[Text] = []
    rows.append(Text(" Tool Details", style=f"bold {TEXT}"))
    hidden_count = max(0, len(records) - 6)
    if hidden_count:
        rows.append(Text(f"  {hidden_count} older tool call(s) hidden", style=DIM))
    if not records:
        rows.append(Text("  No tool calls have finished yet.", style=DIM))
    for record in records[-6:]:
        elapsed = str(record.get("elapsed") or "")
        suffix = f"  {elapsed}" if elapsed else ""
        row = Text()
        row.append("  ● ", style=f"bold {HIGHLIGHT}")
        row.append(str(record.get("display") or "tool"), style=f"bold {TEXT}")
        row.append(suffix, style=SECONDARY)
        rows.extend([row, *_detail_preview_rows(record), Text("")])
    rows.append(Text("┄" * (max_width - 1), style=DIM))
    rows.append(Text(f" ● TOOL DETAILS  {_elapsed_hms(now)}", style=SECONDARY))
    return rows


def _detail_preview_rows(record: dict[str, Any]) -> list[Text]:
    rows: list[Text] = []
    if (tool_input := record.get("input")) not in ({}, None):
        rows.append(Text("    Input:", style=SECONDARY))
        rows.extend(
            Text(f"      {line}", style=DIM)
            for line in format_json_preview(tool_input, max_chars=1200).splitlines()
        )
    if (output := record.get("output")) not in ({}, None, ""):
        rows.append(Text("    Output:", style=SECONDARY))
        rows.extend(
            Text(f"      {line}", style=DIM)
            for line in format_json_preview(output, max_chars=2200).splitlines()
        )
    return rows
