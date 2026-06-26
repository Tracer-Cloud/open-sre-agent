"""Shared helpers for groundcover investigation tools.

All groundcover tools share one client factory, one normalized output envelope,
and one signal-query runner so logs/traces/events/issues/apm stay consistent.
The OpenSRE output envelope is provider-agnostic and never exposes raw MCP
protocol frames to the investigator.

This module lives under ``tools/utils`` (skipped by the tool registry) so it
is shared infrastructure, not a registered tool.
"""

from __future__ import annotations

from typing import Any, cast

from services.groundcover import GroundcoverClient, GroundcoverConfig, GroundcoverToolResult

# Default row cap embedded in seed/example queries. The model can override it,
# but every gcQL example must carry an explicit ``| limit N``.
DEFAULT_ROW_LIMIT = 50
# Safety cap applied to rows we put into the prompt envelope, independent of the
# gcQL ``| limit`` the server enforced. Keeps noisy results bounded.
_ENVELOPE_ROW_CAP = 100
_MAX_FIELD_CHARS = 1000

# Default seed queries: cheap, recent, bounded. Used when the alert payload does
# not carry an explicit query. gcQL leads with the filter directly (no `| filter`
# pipe — that pipe is for post-aggregation conditions on computed aliases), and
# projects with `| fields ...` rather than a bare select-all: the public endpoint
# rejects raw `<filter> | limit N` row pulls that return all columns.
DEFAULT_LOGS_QUERY = "level:error | fields _time, workload, instance, content | limit 50"
DEFAULT_TRACES_QUERY = (
    "status:error | fields _time, workload, span_name, status_code, duration_seconds | limit 50"
)


# Reusable query-guidance preamble embedded in every gcQL tool description.
# This is deliberately redundant with the upstream gcQL reference so OpenSRE
# ships efficient query behavior even when the model never calls the reference.
GCQL_GUIDANCE = (
    "Time range is controlled by start/end/period parameters, NOT in the query. "
    "Keep the window as narrow as the question allows: start with the last 1h (default) and "
    "widen only after an empty/inconclusive result. Wide multi-day scans with selective filters "
    "can time out — '| limit N' caps rows RETURNED, not data SCANNED, so for wide ranges prefer "
    "stats/aggregations over raw row pulls. Queries must start with the filter directly "
    "(e.g. 'level:error | fields _time, content | limit 50') or '*' for match-all — never a bare "
    "'|', and the '| filter' pipe is only for post-aggregation conditions on computed aliases. "
    "Always include '| limit N'. For raw rows, project the fields you need with '| fields ...' "
    "rather than returning all columns; otherwise aggregate with '| stats ...'. "
    "Discover fields before guessing. "
    "Call get_groundcover_query_reference once per session before composing non-trivial gcQL."
)


def groundcover_creds(gc: dict[str, Any]) -> dict[str, Any]:
    """Extract the credential subset a GroundcoverClient needs from a source entry."""
    return {
        "api_key": gc.get("api_key", ""),
        "mcp_url": gc.get("mcp_url", ""),
        "tenant_uuid": gc.get("tenant_uuid", ""),
        "backend_id": gc.get("backend_id", ""),
        "timezone": gc.get("timezone", "UTC"),
    }


def make_client(creds: dict[str, Any]) -> GroundcoverClient | None:
    """Build a GroundcoverClient, or None when credentials are missing/invalid."""
    if not creds.get("api_key"):
        return None
    try:
        config = GroundcoverConfig.model_validate(creds)
    except Exception:
        return None
    if not config.is_configured:
        return None
    return GroundcoverClient(config)


def unavailable(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Standard unavailable envelope (no MCP call was made or it failed)."""
    return {
        "source": source,
        "available": False,
        "data": [],
        "summary": {},
        "truncated": False,
        "error": error,
        **extra,
    }


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
    """Turn a GroundcoverToolResult into the normalized OpenSRE envelope."""
    if not result.success:
        return {
            "source": source,
            "available": False,
            "query": query,
            "time_range": tr,
            "data": [],
            "summary": {},
            "truncated": False,
            "error": result.error or "groundcover query failed",
        }

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


def client_for_source(gc: dict[str, Any]) -> GroundcoverClient | None:
    """Build a GroundcoverClient from a resolved ``groundcover`` source entry."""
    return make_client(groundcover_creds(gc))


def base_extract_params(
    gc: dict[str, Any],
    *,
    default_query: str | None = None,
    include_period: bool = True,
) -> dict[str, Any]:
    """Inject a pre-built client + optional fixture backend, never raw secrets.

    Credentials are bound here into a runtime ``GroundcoverClient`` object so the
    model never sees or can override them. The ``_groundcover_client`` and
    ``groundcover_backend`` keys are runtime objects that the seed-input
    redactor (``^_`` / ``*backend`` patterns) strips before schema validation.
    Only real objects (and schema-declared fields) are included so
    ``additionalProperties: false`` schemas accept the seed input. Tools without
    a time window (entities/monitors/reference) pass ``include_period=False``.
    """
    params: dict[str, Any] = {}
    if include_period:
        params["period"] = gc.get("period", "PT1H")
    if default_query is not None:
        params["query"] = gc.get("default_query") or default_query
    backend = gc.get("_backend")
    if backend is not None:
        params["groundcover_backend"] = backend
    client = client_for_source(gc)
    if client is not None:
        params["_groundcover_client"] = client
    return params
