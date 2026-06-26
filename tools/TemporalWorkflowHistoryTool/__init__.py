"""Temporal workflow execution history tool."""

from __future__ import annotations

from typing import Any

from services.temporal import TemporalClient, TemporalConfig
from tools.base import BaseTool


class TemporalWorkflowHistoryTool(BaseTool):
    """Fetch the event history for a specific workflow execution.

    After identifying a failed workflow via the workflows tool, use this to see
    the ordered sequence of events that tells the story of what happened:
    workflow started, activity scheduled, activity failed, workflow failed, etc.
    This is essential for diagnosing root cause — e.g. "the payment activity
    timed out after 3 retries" or "the child workflow was terminated externally."
    """

    name = "temporal_workflow_history"
    source = "temporal"
    description = (
        "Fetch the event history for a specific Temporal workflow execution. "
        "Shows the ordered sequence of events (started, activity scheduled, "
        "activity failed, workflow failed, etc.) to diagnose why a workflow failed."
    )
    use_cases = [
        "Diagnosing why a specific workflow execution failed",
        "Identifying which activity within a workflow timed out or errored",
        "Tracing the sequence of events leading to workflow failure",
        "Checking if a workflow was terminated externally or failed internally",
        "Finding retry patterns that indicate transient vs persistent failures",
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
                "description": "Temporal namespace.",
            },
            "workflow_id": {
                "type": "string",
                "description": "The workflow ID to fetch history for.",
            },
            "run_id": {
                "type": "string",
                "default": "",
                "description": (
                    "Specific run ID. If omitted, returns history for the latest run "
                    "of the given workflow ID."
                ),
            },
            "next_page_token": {
                "type": "string",
                "default": "",
                "description": "Pagination token from a previous response to fetch the next page.",
            },
        },
        "required": ["base_url", "namespace", "workflow_id"],
    }
    outputs = {
        "events": "Ordered list of history events with eventId, eventTime, and eventType",
        "total": "Number of events returned in this page",
        "next_page_token": "Token for fetching the next page of events",
        "archived": "Whether the history was retrieved from archival storage",
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
        workflow_id: str,
        api_key: str = "",
        namespace: str = "default",
        run_id: str = "",
        next_page_token: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return {
                "source": "temporal",
                "available": False,
                "error": "base_url is required to connect to Temporal.",
                "events": [],
            }
        if not workflow_id:
            return {
                "source": "temporal",
                "available": True,
                "error": "workflow_id is required to fetch execution history.",
                "events": [],
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.get_workflow_history(
                workflow_id=workflow_id,
                run_id=run_id if run_id else None,
                next_page_token=next_page_token if next_page_token else None,
            )
            if not result.get("success"):
                return {
                    "source": "temporal",
                    "available": False,
                    "error": result.get("error", "Unknown error fetching workflow history."),
                    "events": [],
                }
            return {
                "source": "temporal",
                "available": True,
                "events": result["events"],
                "total": result["total"],
                "next_page_token": result["next_page_token"],
                "archived": result["archived"],
            }


temporal_workflow_history = TemporalWorkflowHistoryTool()
