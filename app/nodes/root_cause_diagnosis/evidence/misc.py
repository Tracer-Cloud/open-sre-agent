"""Miscellaneous evidence section builders for root cause diagnosis prompts."""

from __future__ import annotations

import json
from typing import Any


def build_betterstack_section(evidence: dict[str, Any]) -> str:
    betterstack_logs = evidence.get("betterstack_logs", [])
    if not betterstack_logs:
        return ""

    bs_source = evidence.get("betterstack_source", "") or "(unknown source)"
    lines = [f"\nBetter Stack Logs ({len(betterstack_logs)} rows from {bs_source}):\n"]
    for row in betterstack_logs[:15]:
        if not isinstance(row, dict):
            continue
        dt = str(row.get("dt", "")).strip()
        raw = str(row.get("raw", "")).strip()
        if dt:
            lines.append(f"- [{dt}] {raw[:300]}\n")
        elif raw:
            lines.append(f"- {raw[:300]}\n")
    return "".join(lines)


def build_cloudopsbench_section(evidence: dict[str, Any]) -> str:
    entries = evidence.get("cloudopsbench_evidence", [])
    if not entries:
        return ""

    lines = [f"\nCloud-OpsBench Tool Evidence ({len(entries)} actions):\n"]
    for entry in entries[:12]:
        if not isinstance(entry, dict):
            continue
        action_name = entry.get("action_name", "unknown")
        action_input = entry.get("action_input", {})
        cache_hit = "hit" if entry.get("cache_hit") else "miss"
        output = entry.get("output", "")
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False, default=str)
        lines.append(
            f"- {action_name}({json.dumps(action_input, ensure_ascii=False)}) [{cache_hit}]\n"
        )
        lines.append(f"  {output[:1200]}\n")
    lines.append(
        "\nCloud-OpsBench final-answer requirement: when evidence is sufficient, include the "
        "benchmark labels fault_taxonomy, fault_object, and root_cause verbatim in the RCA text.\n"
    )
    return "".join(lines)


def build_openclaw_section(evidence: dict[str, Any]) -> str:
    """Build the OpenClaw conversations / tool-call evidence section."""
    conversations_text = str(evidence.get("openclaw_conversation_context") or "").strip()
    conversations = evidence.get("openclaw_conversations") or []
    detail = evidence.get("openclaw_conversation_detail") or {}
    tool_result = evidence.get("openclaw_tool_call_result") or {}
    available_tools = evidence.get("openclaw_available_tools") or []

    if not any([conversations_text, conversations, detail, tool_result, available_tools]):
        return ""

    lines: list[str] = ["\nOpenClaw Conversation Context:\n"]

    if conversations_text:
        lines.append(f"{conversations_text[:3000]}\n")
    elif conversations:
        for conv in conversations[:5]:
            if not isinstance(conv, dict):
                continue
            title = str(conv.get("title", "")).strip()
            last_msg = str(conv.get("lastMessage", "")).strip()
            if title:
                lines.append(f"  [{title}]\n")
            if last_msg:
                lines.append(f"  {last_msg[:800]}\n\n")

    if detail and isinstance(detail, dict):
        last_msg = str(detail.get("lastMessage", "")).strip()
        if last_msg:
            lines.append(f"\nConversation Detail:\n{last_msg[:800]}\n")

    if tool_result and isinstance(tool_result, dict):
        tool_text = str(tool_result.get("text", "")).strip()
        tool_name = str(tool_result.get("tool", "")).strip()
        if tool_text:
            label = f"OpenClaw tool {tool_name}" if tool_name else "OpenClaw tool call"
            lines.append(f"\n{label} result:\n{tool_text[:800]}\n")

    if available_tools:
        names = [str(t.get("name", "")) for t in available_tools if isinstance(t, dict)]
        lines.append(f"\nOpenClaw available tools: {', '.join(names)}\n")

    return "".join(lines)


def build_alert_annotations_section(alert_annotations: dict[str, Any]) -> str:
    """Build alert annotations evidence section."""
    sections: list[str] = []

    if alert_annotations.get("log_excerpt"):
        sections.append(f"\nLog Excerpt from Alert:\n{alert_annotations['log_excerpt'][:1000]}\n")

    if alert_annotations.get("failed_steps"):
        sections.append(f"\nFailed Steps Summary:\n{alert_annotations['failed_steps']}\n")

    if alert_annotations.get("error"):
        sections.append(f"\nError Message:\n{alert_annotations['error']}\n")

    return "".join(sections)
