"""Query execution and envelope shaping for groundcover tools."""

from __future__ import annotations

from typing import Any, cast

from core.tool_framework.utils import tool_unavailable
from integrations.groundcover.client import GroundcoverClient, GroundcoverToolResult

_ENVELOPE_ROW_CAP = 100
_MAX_FIELD_CHARS = 1000

def unavailable(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """tool_unavailable envelope with data=[], summary={}, truncated=False defaults."""
    return tool_unavailable(source, error, data=[], summary={}, truncated=False, **extra)



def needs_query(source: str) -> dict[str, Any]:
    """Cheap envelope returned when a signal tool is invoked without a gcQL query.

    Used so blind first-round seeding of query tools costs nothing: instead of
    issuing an invalid empty query, the tool tells the model how to call it.
    """
    return {
        "source": source,
        "available": True,
        "query": "",
        "data": [],
        "summary": {},
        "truncated": False,
        "error": None,
        "notes": [
            "Provide a gcQL query to run. Call get_groundcover_query_reference first "
            "for syntax, keep the time window narrow (default 1h), and include | limit N."
        ],
    }



def _truncate_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[: _MAX_FIELD_CHARS - 3] + "..."
    return value



def compact_rows(rows: list[Any], limit: int = _ENVELOPE_ROW_CAP) -> tuple[list[Any], bool]:
    """Cap row count and truncate long string fields. Returns (rows, capped)."""
    capped = len(rows) > limit
    out: list[Any] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            out.append({k: _truncate_value(v) for k, v in row.items()})
        else:
            out.append(_truncate_value(row))
    return out, capped



def time_range(start: str, end: str, period: str) -> dict[str, str]:
    """Echo the requested time window; period defaults to the server default (1h)."""
    return {
        "start": start or "",
        "end": end or "",
        "period": period or ("" if (start and end) else "PT1H"),
    }



def build_envelope(
    source: str,
    query: str,
    result: GroundcoverToolResult,
    *,
    tr: dict[str, str],
) -> dict[str, Any]:
    """Turn a GroundcoverToolResult into an envelope dict with source/available/query/
    time_range/data/summary/truncated/error (+ notes when present)."""
    if not result.success:
        return tool_unavailable(
            source,
            result.error or "groundcover query failed",
            query=query,
            time_range=tr,
            data=[],
            summary={},
            truncated=False,
        )

    data = result.data
    truncated = any("truncat" in note.lower() for note in result.notes)
    summary: dict[str, Any] = {}
    if isinstance(data, list):
        rows, capped = compact_rows(data)
        summary = {"returned": len(rows), "total_in_response": len(data)}
        truncated = truncated or capped
        data_out: Any = rows
    else:
        data_out = data if data is not None else []

    envelope: dict[str, Any] = {
        "source": source,
        "available": True,
        "query": query,
        "time_range": tr,
        "data": data_out,
        "summary": summary,
        "truncated": truncated,
        "error": None,
    }
    if result.notes:
        envelope["notes"] = result.notes
    return envelope



def run_signal_query(
    *,
    source: str,
    mcp_tool: str,
    client: GroundcoverClient | None,
    query: str,
    start: str = "",
    end: str = "",
    period: str = "",
    backend: Any = None,
    extra_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared runner for gcQL signal tools (logs/traces/events/issues/apm).

    ``client`` is a pre-built :class:`GroundcoverClient` (or None) injected via
    ``extract_params``; credentials never travel through the model-facing tool
    arguments. When ``backend`` is provided (synthetic harness), the call
    short-circuits to the fixture backend. An empty query yields a cheap
    ``needs_query`` envelope without any MCP round trip.
    """
    if backend is not None:
        method = getattr(backend, mcp_tool, None)
        if callable(method):
            return cast(
                "dict[str, Any]",
                method(query=query, start=start, end=end, period=period),
            )
        return unavailable(source, f"groundcover backend does not implement {mcp_tool}")

    if client is None:
        return unavailable(source, "groundcover integration not configured")
    if not query.strip():
        return needs_query(source)

    args: dict[str, Any] = {"query": query}
    if start:
        args["start"] = start
    if end:
        args["end"] = end
    if period:
        args["period"] = period
    if extra_args:
        args.update(extra_args)

    result = client.call_tool(mcp_tool, args)
    return build_envelope(source, query, result, tr=time_range(start, end, period))
