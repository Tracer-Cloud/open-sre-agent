"""Evidence mappers for the PostHog MCP-backed tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: Bound a name_filter or a generic tool result echoed into a report summary --
#: both are caller/upstream-supplied and unbounded.
_SUMMARY_TRUNCATE_LEN = 120

#: Bound the dispatched MCP tool name -- caller-controlled and unbounded --
#: before it flows into the summary, label, and evidence source key.
_TOOL_NAME_TRUNCATE_LEN = 60


def _safe_tool_name(tool_name: str) -> str:
    return truncate(tool_name.replace("\n", " "), _TOOL_NAME_TRUNCATE_LEN)


def _next_unique_source(evidence: dict[str, Any], base: str) -> str:
    """Disambiguate repeat calls to a generic dispatcher tool.

    ``record_evidence_entry`` lets the first entry for a given ``source`` win,
    but ``call_posthog_tool`` is a single dispatcher that can be invoked many
    times per investigation with different underlying PostHog tools/arguments
    -- reusing one source key would silently drop every call after the first.
    """
    entries = evidence.get("catalog_entries")
    if not isinstance(entries, list):
        return base
    existing = {e.get("source") for e in entries if isinstance(e, dict)}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}#{suffix}" in existing:
        suffix += 1
    return f"{base}#{suffix}"


def map_list_posthog_tools(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the matched tool count from the bounded MCP catalog listing.

    ``build_mcp_tool_listing`` caps ``returned_tools`` at 80 regardless of how
    many actually matched -- a real truncation signal, not an inferred one --
    so ``matched_tools`` is qualified with "+" whenever the listing shown is
    smaller than what actually matched.
    """
    if not output.get("available"):
        return
    returned = output.get("returned_tools", 0)
    if not returned:
        return
    matched = output.get("matched_tools", returned)
    count_label = f"{matched}+" if returned < matched else str(matched)
    summary = f"{count_label} tool(s) listed"
    name_filter = output.get("name_filter")
    if name_filter:
        summary += f" matching '{truncate(str(name_filter), _SUMMARY_TRUNCATE_LEN)}'"
    record_evidence_entry(
        evidence,
        source="list_posthog_tools",
        label="PostHog MCP Tools",
        summary=summary,
    )


def map_call_posthog_tool(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the result of a dispatched PostHog MCP tool call.

    The underlying MCP tool varies per call (HogQL rows, feature-flag lists,
    docs text, ...), so the summary shape follows whichever result field is
    populated: row count for ``execute-sql``/``query-run``, otherwise the
    response text/structured content, bounded.
    """
    if not output.get("available"):
        return
    raw_tool_name = str(output.get("tool") or tool_input.get("tool_name") or "").strip()
    tool_name = _safe_tool_name(raw_tool_name) if raw_tool_name else ""
    results = output.get("results")
    text = output.get("text")
    structured = output.get("structured_content")
    content = output.get("content")

    if isinstance(results, list):
        if not results:
            return
        summary = f"{len(results)} row(s)"
    elif text:
        summary = truncate(str(text).replace("\n", " ").strip(), _SUMMARY_TRUNCATE_LEN)
    elif structured is not None:
        summary = truncate(str(structured).replace("\n", " ").strip(), _SUMMARY_TRUNCATE_LEN)
    elif isinstance(content, list) and content:
        summary = f"{len(content)} item(s)"
    else:
        return

    if tool_name:
        summary += f" (tool: {tool_name})"
    base_source = f"call_posthog_tool:{tool_name}" if tool_name else "call_posthog_tool"
    record_evidence_entry(
        evidence,
        source=_next_unique_source(evidence, base_source),
        label=f"PostHog MCP: {tool_name}" if tool_name else "PostHog MCP",
        summary=summary,
    )
