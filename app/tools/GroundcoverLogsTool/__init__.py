"""groundcover logs query tool (gcQL over query_logs)."""

from __future__ import annotations

from typing import Any

from app.services.groundcover import GroundcoverClient
from app.tools.tool_decorator import tool
from app.tools.utils.availability import groundcover_available_or_backend
from app.tools.utils.groundcover import (
    DEFAULT_LOGS_QUERY,
    GCQL_GUIDANCE,
    base_extract_params,
    run_signal_query,
)

_SOURCE = "groundcover_logs"
_MCP_TOOL = "query_logs"

_QUERY_DESCRIPTION = (
    "gcQL query. Lead with the filter directly (not a '| filter' pipe) and include "
    "'| limit N'. Project raw rows with '| fields ...' rather than a bare select-all. "
    "Examples: 'level:error | fields _time, workload, instance, content | limit 50'; "
    "'workload:checkout level:error | fields _time, instance, content | limit 50'; "
    "'* | stats by (workload) count() if (level:error) as errors | sort by (errors desc) "
    "| limit 20'."
)


def _is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client + seed query, never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), default_query=DEFAULT_LOGS_QUERY)


@tool(
    name="query_groundcover_logs",
    display_name="groundcover logs",
    source="groundcover",
    tags=("logs", "observability"),
    cost_tier="moderate",
    description=(
        "Search groundcover logs with gcQL. Use for application errors, exceptions, and service "
        "log events. " + GCQL_GUIDANCE + " Discover fields with '* | field_names' or by calling "
        "get_groundcover_query_reference."
    ),
    use_cases=[
        "Finding error/exception logs for a workload or namespace",
        "Correlating log spikes with a groundcover monitor issue",
        "Counting errors per workload with a single stats query over a narrow window",
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
def query_groundcover_logs(
    query: str = "",
    start: str = "",
    end: str = "",
    period: str = "",
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search groundcover logs with gcQL and return the normalized OpenSRE envelope.

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
