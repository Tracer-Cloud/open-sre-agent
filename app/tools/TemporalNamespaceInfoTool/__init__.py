"""Temporal namespace health overview tool."""

from __future__ import annotations

from typing import Any

from app.services.temporal import TemporalClient, TemporalConfig
from app.tools.base import BaseTool


class TemporalNamespaceInfoTool(BaseTool):
    """Fetch namespace state and workflow counts grouped by execution status.

    This is the first tool to call when investigating Temporal-related incidents.
    It provides a high-level health snapshot: is the namespace active, and how
    many workflows are running vs failed vs timed out. Use this to determine
    whether something is wrong before drilling into specific workflows.
    """

    name = "temporal_namespace_info"
    source = "temporal"
    description = (
        "Fetch Temporal namespace health overview: namespace state and workflow "
        "execution counts grouped by status (Running, Failed, TimedOut, etc.). "
        "Use as the first investigation step to assess overall namespace health."
    )
    use_cases = [
        "Getting a high-level health snapshot of a Temporal namespace",
        "Checking if a namespace is active or deprecated/deleted",
        "Counting how many workflows are currently running, failed, or timed out",
        "Determining whether a Temporal incident is widespread or isolated",
        "Initial triage before drilling into specific workflow failures",
    ]
    requires = ["base_url", "namespace"]
    injected_params = ["base_url", "api_key", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Temporal server base URL.",
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Temporal API key. Empty for unauthenticated self-hosted clusters.",
            },
            "namespace": {
                "type": "string",
                "default": "default",
                "description": "Temporal namespace to inspect.",
            },
        },
        "required": ["base_url", "namespace"],
    }
    outputs = {
        "name": "Namespace name",
        "state": "Namespace state (REGISTERED, DEPRECATED, DELETED)",
        "workflow_count": "Total workflow executions across all statuses",
        "groups": "Breakdown of workflow counts by execution status",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        temporal = sources.get("temporal", {})
        return bool(temporal.get("base_url"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {
            "base_url": temporal.get("base_url", ""),
            "api_key": temporal.get("api_key", ""),
            "namespace": temporal.get("namespace", "default"),
        }

    def run(
        self,
        base_url: str,
        api_key: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return {
                "source": "temporal",
                "available": False,
                "error": "base_url is required to connect to Temporal.",
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.get_namespace_info()
            if not result.get("success"):
                return {
                    "source": "temporal",
                    "available": False,
                    "error": result.get("error", "Unknown error fetching namespace info."),
                }
            return {
                "source": "temporal",
                "available": True,
                "name": result["name"],
                "state": result["state"],
                "workflow_count": result["workflow_count"],
                "groups": result["groups"],
            }


temporal_namespace_info = TemporalNamespaceInfoTool()
