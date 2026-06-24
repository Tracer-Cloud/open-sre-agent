"""groundcover traces query tool (gcQL over query_traces)."""

from __future__ import annotations

from typing import Any

from app.services.groundcover import GroundcoverClient
from app.tools.tool_decorator import tool
from app.tools.utils.availability import groundcover_available_or_backend
from app.tools.utils.groundcover import (
    DEFAULT_TRACES_QUERY,
    GCQL_GUIDANCE,
    base_extract_params,
    run_signal_query,
)

_SOURCE = "groundcover_traces"
_MCP_TOOL = "query_traces"

_QUERY_DESCRIPTION = (
    "gcQL query. Lead with the filter directly (not a '| filter' pipe) and include "
    "'| limit N'. Project raw spans with '| fields ...' (a bare select-all '| limit N' is "
    "rejected for traces); otherwise aggregate with '| stats ...'. Examples: "
    "'workload:checkout duration_seconds>0.5 | fields _time, span_name, duration_seconds "
    "| sort by (duration_seconds desc) | limit 50'; "
    "'status_code>=500 | stats by (workload) count() as errors | sort by (errors desc) "
    "| limit 20'; "
    "'span_type:mysql status:error | fields _time, span_name, status_code | limit 50'."
)


def _is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client + seed query, never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), default_query=DEFAULT_TRACES_QUERY)


@tool(
    name="query_groundcover_traces",
    display_name="groundcover traces",
    source="groundcover",
    tags=("traces", "observability"),
    cost_tier="moderate",
    description=(
        "Query groundcover traces/spans with gcQL. Use to find slow spans, failing spans, and "
        "request correlations across services. " + GCQL_GUIDANCE + " Discover fields with "
        "'* | field_names'. For traces, free text needs '*:*term*' or 'field:*term*' (no bare "
        "keywords). Error filtering by span type: HTTP spans use 'status_code>=500' (or "
        "'status_code>399'); databases/gRPC/messaging and any span type use 'status:error' "
        "(universal). Never use 'status_code>399' on non-HTTP spans — their codes differ. Key "
        "fields: span_name (endpoint), workload (caller), server (callee), duration_seconds, status."
    ),
    use_cases=[
        "Finding the slowest spans for a workload (sort by duration_seconds desc)",
        "Locating 5xx/erroring spans for a service or endpoint",
        "Aggregating error rate and p95 latency per workload with one stats query",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _QUERY_DESCRIPTION},
            "start": {"type": "string", "description": "RFC3339 start time (optional)"},
            "end": {"type": "string", "description": "RFC3339 end time (optional)"},
            "period": {
                "type": "string",
                "description": "ISO-8601 duration window, e.g. PT1H (default).",
                "default": "PT1H",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    is_available=_is_available,
    extract_params=_extract_params,
)
def query_groundcover_traces(
    query: str = "",
    start: str = "",
    end: str = "",
    period: str = "",
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query groundcover traces/spans with gcQL and return the normalized OpenSRE envelope.

    Credentials never travel through the model-facing arguments: ``extract_params``
    binds a pre-built :class:`GroundcoverClient` into ``_groundcover_client`` (and an
    optional synthetic ``groundcover_backend``), both stripped from seed input by the
    redactor. An empty query yields a cheap guidance envelope with no MCP round trip.
    """
    return run_signal_query(
        source=_SOURCE,
        mcp_tool=_MCP_TOOL,
        client=_groundcover_client,
        query=query,
        start=start,
        end=end,
        period=period,
        backend=groundcover_backend,
    )
