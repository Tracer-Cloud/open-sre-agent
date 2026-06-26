# ======== from tools/temporal_namespace_info_tool/ ========

"""Temporal namespace health overview tool."""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool
from vendors.temporal.client import TemporalClient, TemporalConfig


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


# ======== from tools/temporal_task_queue_tool/ ========

"""Temporal task queue description tool."""


from tools.base import BaseTool


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


# ======== from tools/temporal_workflow_history_tool/ ========

"""Temporal workflow execution history tool."""


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


# ======== from tools/temporal_workflows_tool/ ========

"""Temporal workflow executions listing tool."""


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
