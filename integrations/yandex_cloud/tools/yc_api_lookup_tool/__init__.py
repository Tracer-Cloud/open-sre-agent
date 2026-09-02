"""Find the REST endpoint for any Yandex Cloud resource.

Yandex Cloud has ~940 readable endpoints across 68 services. Only a handful get
a purpose-built tool, and guessing a path for the rest costs a round trip that
returns a bare 404 with nothing to learn from. This looks the path up in the
index generated from the official protobuf definitions, so the agent reaches for
``execute_yc_operation`` already knowing the exact service and path.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.yandex_cloud.api_index import (
    DEFAULT_LIMIT,
    endpoint_count,
    known_services,
    search,
)
from integrations.yandex_cloud.availability import yc_available_or_backend

SOURCE = "yandex_cloud"


@tool(
    name="find_yc_api",
    display_name="Yandex Cloud",
    source=SOURCE,
    surfaces=(ToolSurface.ACTION,),
    description=(
        "Find the exact REST path for any Yandex Cloud resource, across every "
        "service the API exposes — not just the ones with a dedicated tool. "
        "Search by resource name ('clusters', 'security groups', 'certificates'), "
        "by service, or both. Returns paths ready to pass to execute_yc_operation. "
        "Use this instead of guessing a path, and instead of the `yc` CLI, which "
        "is not how this agent reaches Yandex Cloud. Indexes Yandex Cloud "
        "resources only — Kubernetes pods and their logs are not in it."
    ),
    use_cases=[
        "Reading a resource type that has no dedicated tool",
        "Finding the list endpoint for a service before fetching one resource",
        "Checking whether the API can read something at all",
        "Resolving the service id execute_yc_operation needs",
        "Listing which services exist, by calling with no query",
    ],
    requires=[],
    outputs={
        "endpoints": (
            "matching REST paths with the service each belongs to, and the query "
            "parameters each accepts, with allowed values where the API fixes them"
        ),
        "count": "how many matched",
        "services": "service ids matched, for narrowing a broad search",
    },
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Words that must all appear in the service, path or method — "
                    "e.g. 'postgresql clusters', 'security groups', 'node group'."
                ),
                "default": "",
            },
            "service": {
                "type": "string",
                "description": (
                    "Restrict to one service id, e.g. 'compute', 'managed-postgresql'."
                ),
                "default": "",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum paths to return.",
                "default": DEFAULT_LIMIT,
            },
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
)
def find_yc_api(query: str = "", service: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Look up readable REST endpoints by resource, service or free text."""
    if not query.strip() and not service.strip():
        # No question yet — answer the prior one: which services exist at all.
        return {
            "source": SOURCE,
            "endpoints": [],
            "count": 0,
            "services": list(known_services()),
            "next_step": (
                "Call find_yc_api again with a resource in plain words, or with "
                "service= one of the ids above, to get exact REST paths."
            ),
        }

    matches = search(query, service=service, limit=limit)
    if not matches:
        return {
            "source": SOURCE,
            "endpoints": [],
            "count": 0,
            "services": list(known_services()),
            "note": (
                f"Nothing in the {endpoint_count()} indexed read endpoints matches "
                f"{query!r}. Try one word instead of a phrase, or narrow by service. "
                "The index covers reads only — Yandex Cloud is never written to from here."
            ),
        }

    return {
        "source": SOURCE,
        "endpoints": [match.as_payload() for match in matches],
        "count": len(matches),
        "services": sorted({match.service for match in matches}),
        "next_step": (
            "Call execute_yc_operation with the service and path shown. Replace any "
            "{placeholder} with a real id, which a list endpoint on the same service "
            "will give you, and pass the listed params — an endpoint that names one "
            "usually rejects the call without it."
        ),
    }


__all__ = ["find_yc_api"]
