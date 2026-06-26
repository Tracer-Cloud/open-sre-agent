"""Temporal workflow executions listing tool."""

from __future__ import annotations

from typing import Any

from services.temporal import TemporalClient, TemporalConfig
from tools.base import BaseTool


class TemporalWorkflowsTool(BaseTool):
    """List recent workflow executions with status and failure reason.

    After identifying a problem via namespace info (e.g. "8 workflows failed"),
    use this tool to see which specific workflows failed, when they started and
    closed, what type they are, and which task queue they ran on. The task queue
    name from these results feeds into the task queue tool for worker health checks.
    """

    name = "temporal_workflows"
    source = "temporal"
    description = (
        "List recent Temporal workflow executions showing workflowId, type, status, "
        "taskQueue, and timing. Use after namespace info reveals failures, to identify "
        "which specific workflows failed and on which task queues."
    )
    use_cases = [
        "Listing recent workflow executions to find failures",
        "Identifying which workflow types are failing",
        "Discovering which task queues are involved in failures",
        "Getting workflowId and runId for detailed history inspection",
        "Correlating workflow failures with infrastructure alerts",
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
                "description": "Temporal namespace to query.",
            },
            "next_page_token": {
                "type": "string",
                "default": "",
                "description": "Pagination token from a previous response to fetch the next page.",
            },
        },
        "required": ["base_url", "namespace"],
    }
    outputs = {
        "executions": "List of workflow executions with workflowId, type, status, taskQueue, and timing",
        "total": "Number of executions returned in this page",
        "next_page_token": "Token for fetching the next page of results",
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
        next_page_token: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return {
                "source": "temporal",
                "available": False,
                "error": "base_url is required to connect to Temporal.",
                "executions": [],
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            token = next_page_token if next_page_token else None
            result = client.list_workflow_executions(next_page_token=token)
            if not result.get("success"):
                return {
                    "source": "temporal",
                    "available": False,
                    "error": result.get("error", "Unknown error listing workflow executions."),
                    "executions": [],
                }
            return {
                "source": "temporal",
                "available": True,
                "executions": result["executions"],
                "total": result["total"],
                "next_page_token": result["next_page_token"],
            }


temporal_workflows = TemporalWorkflowsTool()
