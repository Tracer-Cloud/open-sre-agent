"""groundcover query-language (gcQL) reference tool."""

from __future__ import annotations

from typing import Any, cast

from services.groundcover import GroundcoverClient
from tools.tool_decorator import tool
from tools.utils.availability import groundcover_available_or_backend
from tools.utils.groundcover import base_extract_params

_SOURCE = "groundcover_query_reference"


def _is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client (no time window), never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), include_period=False)


@tool(
    name="get_groundcover_query_reference",
    display_name="groundcover query reference",
    source="groundcover",
    tags=("observability", "reference"),
    cost_tier="cheap",
    surfaces=("investigation", "chat"),
    description=(
        "Get the groundcover Query Language (gcQL) reference: operators, functions, pipes, and "
        "query patterns. Call this ONCE before writing gcQL for any query_groundcover_* tool. "
        "Reading it first prevents malformed and overly expensive queries."
    ),
    use_cases=[
        "Before composing any non-trivial gcQL query for groundcover logs/traces/metrics/apm",
        "When unsure about stats/sort/filter syntax or pipe operators",
        "To recall the performance and time-window guidance for efficient queries",
    ],
    requires=[],
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    is_available=_is_available,
    extract_params=_extract_params,
)
def get_groundcover_query_reference(
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return the cached gcQL reference skill content."""
    if groundcover_backend is not None and hasattr(groundcover_backend, "get_query_reference"):
        return cast("dict[str, Any]", groundcover_backend.get_query_reference())

    if _groundcover_client is None:
        return {
            "source": _SOURCE,
            "available": False,
            "reference": "",
            "error": "groundcover integration not configured",
        }
    result = _groundcover_client.get_query_reference()
    if not result.get("success"):
        return {
            "source": _SOURCE,
            "available": False,
            "reference": "",
            "error": result.get("error", "could not fetch gcQL reference"),
        }
    return {
        "source": _SOURCE,
        "available": True,
        "reference": result.get("reference", ""),
        "cached": result.get("cached", False),
        "error": None,
    }
