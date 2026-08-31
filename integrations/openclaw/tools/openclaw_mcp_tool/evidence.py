"""Evidence mappers for OpenClaw read tools.

Summaries only — conversation bodies, MCP text, and tool schemas stay out of
the catalog so they do not compete for later-turn context.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry

_MAX_FIELD_CHARS = 80


def _available(output: dict[str, Any]) -> bool:
    return output.get("available") is not False


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_structured(value: object) -> bool:
    if isinstance(value, dict):
        return bool(value)
    return isinstance(value, list) and bool(value)


def _bounded_field(value: object) -> str:
    """Collapse whitespace and cap OpenClaw metadata used in catalog summaries."""
    text = " ".join(str(value).split())
    if len(text) <= _MAX_FIELD_CHARS:
        return text
    return text[: _MAX_FIELD_CHARS - 1].rstrip() + "…"


def _conversation_label(structured: object, tool_input: dict[str, Any]) -> str:
    if isinstance(structured, dict):
        ident = (
            structured.get("id")
            or structured.get("conversationId")
            or structured.get("session_key")
        )
        title = structured.get("title") or structured.get("derivedTitle")
        parts: list[str] = []
        if ident:
            bounded_ident = _bounded_field(ident)
            if bounded_ident:
                parts.append(bounded_ident)
        if title:
            bounded_title = _bounded_field(title)
            if bounded_title:
                parts.append(bounded_title)
        if parts:
            return " — ".join(parts)
    conversation_id = _bounded_field(tool_input.get("conversation_id") or "")
    if conversation_id:
        return conversation_id
    return "conversation loaded"


def _call_size_hint(output: dict[str, Any]) -> str | None:
    structured = output.get("structured_content")
    if isinstance(structured, list) and structured:
        n = len(structured)
        return f"{n} structured item" if n == 1 else f"{n} structured items"
    if isinstance(structured, dict) and structured:
        return "structured result"
    content = output.get("content")
    if isinstance(content, list) and content:
        n = len(content)
        return f"{n} content item" if n == 1 else f"{n} content items"
    if _nonempty_text(output.get("text")):
        return "text returned"
    return None


def map_list_openclaw_tools(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite a non-empty OpenClaw MCP tool listing."""
    if not _available(output):
        return
    tools = output.get("tools")
    if not isinstance(tools, list) or not tools:
        return
    returned = output.get("returned_tools", len(tools))
    try:
        returned_n = int(returned)
    except (TypeError, ValueError):
        returned_n = len(tools)
    total = output.get("total_tools")
    try:
        total_n = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_n = None
    summary = f"{returned_n} tools listed"
    if total_n is not None and total_n != returned_n:
        summary = f"{returned_n} tools listed ({total_n} total)"
    record_evidence_entry(
        evidence,
        source="list_openclaw_tools",
        label="OpenClaw Tools",
        summary=summary,
    )


def map_search_openclaw_conversations(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite matching OpenClaw conversations without inlining their transcripts."""
    if not _available(output):
        return
    conversations = output.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        return
    count = len(conversations)
    word = "conversation" if count == 1 else "conversations"
    summary = f"{count} {word}"
    search = str(output.get("search") or tool_input.get("search") or "").strip()
    if search:
        summary += f" matching '{_bounded_field(search)}'"
    record_evidence_entry(
        evidence,
        source="search_openclaw_conversations",
        label="OpenClaw Conversations",
        summary=summary,
    )


def map_get_openclaw_conversation(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite that one OpenClaw conversation was retrieved."""
    if not _available(output):
        return
    structured = output.get("structured_content")
    if not _nonempty_structured(structured) and not _nonempty_text(output.get("text")):
        return
    record_evidence_entry(
        evidence,
        source="get_openclaw_conversation",
        label="OpenClaw Conversation",
        summary=_conversation_label(structured, tool_input),
    )


def map_call_openclaw_tool(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite a successful OpenClaw MCP tool result without dumping the payload."""
    if not _available(output):
        return
    hint = _call_size_hint(output)
    if hint is None:
        return
    tool_name = _bounded_field(output.get("tool") or tool_input.get("tool_name") or "")
    summary = f"{tool_name} returned {hint}" if tool_name else hint
    record_evidence_entry(
        evidence,
        source="call_openclaw_tool",
        label="OpenClaw Tool Result",
        summary=summary,
    )
