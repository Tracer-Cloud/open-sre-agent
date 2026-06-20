"""Temporal task queue description tool."""

from __future__ import annotations

from typing import Any

from app.services.temporal import TemporalClient, TemporalConfig
from app.tools.base import BaseTool


class TemporalTaskQueueTool(BaseTool):
    """Describe a task queue's pollers and backlog stats.

    After identifying failed workflows and the task queues they ran on, use this
    tool to check worker health. Empty pollers mean workers are down. A growing
    backlog (high approximateBacklogCount, tasksAddRate > tasksDispatchRate)
    means workers can't keep up. Stale lastAccessTime on pollers indicates
    workers have stopped heartbeating.

    Task queue names are discovered from workflow executions — each execution
    reports which task queue it ran on. The Temporal API does not expose a
    "list all task queues" endpoint.
    """

    name = "temporal_task_queue"
    source = "temporal"
    description = (
        "Describe a Temporal task queue: active worker pollers and backlog stats "
        "(approximate count, age, add/dispatch rates). Use after identifying failed "
        "workflows to check if workers are down or overwhelmed on that queue."
    )
    use_cases = [
        "Checking if workers are polling a task queue (are they alive?)",
        "Detecting worker outages (empty pollers list = no workers connected)",
        "Identifying backlog buildup (tasks queued faster than dispatched)",
        "Correlating workflow timeouts with stale worker heartbeats",
        "Verifying worker capacity after a deployment or scaling event",
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
            "task_queue_name": {
                "type": "string",
                "description": (
                    "Name of the task queue to inspect. Obtain this from the taskQueue "
                    "field in workflow execution results."
                ),
            },
        },
        "required": ["base_url", "namespace", "task_queue_name"],
    }
    outputs = {
        "pollers": "List of active worker pollers with identity, lastAccessTime, and ratePerSecond",
        "stats": "Backlog metrics: approximateBacklogCount, approximateBacklogAge, tasksAddRate, tasksDispatchRate",
        "total": "Number of active pollers on this queue",
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
        task_queue_name: str,
        api_key: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return {
                "source": "temporal",
                "available": False,
                "error": "base_url is required to connect to Temporal.",
                "pollers": [],
                "stats": {},
            }
        if not task_queue_name:
            return {
                "source": "temporal",
                "available": True,
                "error": "task_queue_name is required. Get it from the taskQueue field in workflow execution results.",
                "pollers": [],
                "stats": {},
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.describe_task_queue(task_queue_name=task_queue_name)
            if not result.get("success"):
                return {
                    "source": "temporal",
                    "available": False,
                    "error": result.get("error", "Unknown error describing task queue."),
                    "pollers": [],
                    "stats": {},
                }
            return {
                "source": "temporal",
                "available": True,
                "pollers": result["pollers"],
                "stats": result["stats"],
                "total": result["total"],
            }


temporal_task_queue = TemporalTaskQueueTool()
