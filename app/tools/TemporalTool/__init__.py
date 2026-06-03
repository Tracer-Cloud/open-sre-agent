from __future__ import annotations

from typing import Any

from app.services.temporal.client import TemporalClient, TemporalClientError
from app.tools.base import BaseTool


def _attrs_key(event_type: str) -> str:
    _map = {
        "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED": "workflowExecutionStartedEventAttributes",
        "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED": "workflowExecutionCompletedEventAttributes",
        "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED": "workflowExecutionFailedEventAttributes",
        "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT": "workflowExecutionTimedOutEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED": "activityTaskScheduledEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_STARTED": "activityTaskStartedEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_COMPLETED": "activityTaskCompletedEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_FAILED": "activityTaskFailedEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT": "activityTaskTimedOutEventAttributes",
        "EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED": "activityTaskCancelRequestedEventAttributes",
    }
    return _map.get(event_type, "")


class TemporalListWorkflowsTool(BaseTool):
    name = "temporal_list_workflows"
    source = "temporal"
    description = (
        "List recent Temporal workflow executions. "
        "Use this to identify failed, timed-out, or stuck workflows in a namespace. "
        "Supports Temporal visibility queries to filter by status, type, or time range."
    )
    use_cases = [
        "Finding all failed Temporal workflows in a namespace",
        "Listing stuck or running workflows during an incident",
        "Filtering workflows by type or execution status",
        "Getting a first overview of workflow health in a namespace",
    ]
    requires = ["host"]
    input_schema = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Temporal server hostname."},
            "namespace": {"type": "string", "default": "default", "description": "Temporal namespace to query."},
            "query": {"type": "string", "default": "", "description": "Temporal visibility query string."},
            "page_size": {"type": "integer", "default": 20, "description": "Maximum number of workflow executions to return (1-50)."},
        },
        "required": ["host"],
    }
    outputs = {
        "executions": "List of workflow executions with status and timing",
        "failed_executions": "Subset of executions in FAILED or TIMED_OUT state",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bool(sources.get("temporal", {}).get("connection_verified"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {"host": temporal.get("host", ""), "namespace": temporal.get("namespace", "default"), "query": "ExecutionStatus='Failed'", "page_size": 20}

    def run(self, host: str, namespace: str = "default", query: str = "", page_size: int = 20, **_kwargs: Any) -> dict[str, Any]:
        if not (host or "").strip():
            return {"source": "temporal", "available": False, "error": "host is required to connect to Temporal.", "executions": [], "failed_executions": []}
        try:
            from app.integrations.temporal import TemporalConfig
            client = TemporalClient(TemporalConfig(host=host, namespace=namespace))
            executions = client.list_workflows(query=query, page_size=min(page_size, 50))
        except TemporalClientError as exc:
            return {"source": "temporal", "available": False, "error": str(exc), "executions": [], "failed_executions": []}
        failed = [ex for ex in executions if ex.get("status", "") in ("FAILED", "TIMED_OUT", "TERMINATED")]
        return {"source": "temporal", "available": True, "executions": executions, "total": len(executions), "failed_executions": failed, "total_failed": len(failed)}


class TemporalWorkflowHistoryTool(BaseTool):
    name = "temporal_workflow_history"
    source = "temporal"
    description = (
        "Fetch the full event history for a specific Temporal workflow execution. "
        "Use this to diagnose failures: the history shows every activity attempt, "
        "retry, timeout, signal, and the final failure cause with stack traces."
    )
    use_cases = [
        "Diagnosing why a specific Temporal workflow failed",
        "Finding which activity timed out in a failed workflow",
        "Checking retry attempts and failure messages",
        "Getting the stack trace for a failed workflow execution",
    ]
    requires = ["host", "workflow_id", "run_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Temporal server hostname."},
            "namespace": {"type": "string", "default": "default", "description": "Temporal namespace to query."},
            "workflow_id": {"type": "string", "description": "The workflow ID to inspect."},
            "run_id": {"type": "string", "description": "The run ID of the specific execution."},
            "max_event_count": {"type": "integer", "default": 50, "description": "Maximum number of history events to return (1-200)."},
        },
        "required": ["host", "workflow_id", "run_id"],
    }
    outputs = {
        "events": "List of workflow history events",
        "failure_events": "Events containing failure details",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bool(sources.get("temporal", {}).get("connection_verified"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {"host": temporal.get("host", ""), "namespace": temporal.get("namespace", "default"), "workflow_id": temporal.get("workflow_id", ""), "run_id": temporal.get("run_id", ""), "max_event_count": 50}

    def run(self, host: str, workflow_id: str, run_id: str, namespace: str = "default", max_event_count: int = 50, **_kwargs: Any) -> dict[str, Any]:
        if not (host or "").strip():
            return {"source": "temporal", "available": False, "error": "host is required to connect to Temporal.", "events": [], "failure_events": []}
        try:
            from app.integrations.temporal import TemporalConfig
            client = TemporalClient(TemporalConfig(host=host, namespace=namespace))
            events = client.get_workflow_history(workflow_id=workflow_id, run_id=run_id, max_event_count=min(max_event_count, 200))
        except TemporalClientError as exc:
            return {"source": "temporal", "available": False, "error": str(exc), "events": [], "failure_events": []}
        failure_events = [ev for ev in events if "FAILED" in ev.get("eventType", "") or "TIMED_OUT" in ev.get("eventType", "")]
        return {"source": "temporal", "available": True, "events": events, "total_events": len(events), "failure_events": failure_events, "total_failure_events": len(failure_events)}


class TemporalTaskQueueTool(BaseTool):
    name = "temporal_task_queue"
    source = "temporal"
    description = (
        "Fetch Temporal task queue info: which workers are polling, their identity, "
        "last access time, and whether the queue is drained. "
        "Use this when investigating worker crashes, missing workers, or stuck workflows."
    )
    use_cases = [
        "Checking if workers are polling a Temporal task queue",
        "Identifying missing or crashed workers causing stuck workflows",
        "Auditing task queue backlog during an incident",
        "Verifying worker identity and last heartbeat time",
    ]
    requires = ["host", "task_queue"]
    input_schema = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Temporal server hostname."},
            "namespace": {"type": "string", "default": "default", "description": "Temporal namespace to query."},
            "task_queue": {"type": "string", "description": "Name of the task queue to inspect."},
        },
        "required": ["host", "task_queue"],
    }
    outputs = {
        "pollers": "List of workers currently polling the task queue",
        "unhealthy_queues": "Task queues with no active pollers",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bool(sources.get("temporal", {}).get("connection_verified"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {"host": temporal.get("host", ""), "namespace": temporal.get("namespace", "default"), "task_queue": temporal.get("task_queue", "")}

    def run(self, host: str, task_queue: str, namespace: str = "default", **_kwargs: Any) -> dict[str, Any]:
        if not (host or "").strip():
            return {"source": "temporal", "available": False, "error": "host is required to connect to Temporal.", "pollers": [], "unhealthy_queues": []}
        try:
            from app.integrations.temporal import TemporalConfig
            client = TemporalClient(TemporalConfig(host=host, namespace=namespace))
            data = client.list_task_queues(task_queue=task_queue)
        except TemporalClientError as exc:
            return {"source": "temporal", "available": False, "error": str(exc), "pollers": [], "unhealthy_queues": []}
        pollers = data.get("pollers", [])
        status = data.get("taskQueueStatus", {})
        result: dict[str, Any] = {
            "source": "temporal", "available": True, "task_queue": task_queue,
            "poller_count": len(pollers),
            "pollers": [{"identity": p.get("identity", ""), "last_access_time": p.get("lastAccessTime", ""), "rate_per_second": p.get("ratePerSecond", 0)} for p in pollers],
            "backlog_count": status.get("backlogCountHint", 0),
            "unhealthy_queues": [],
        }
        if not pollers:
            result["unhealthy_queues"] = [task_queue]
            result["warning"] = "No workers are currently polling this task queue. Workflows may be stuck waiting for workers."
        return result


class TemporalNamespaceMetricsTool(BaseTool):
    name = "temporal_namespace_metrics"
    source = "temporal"
    description = (
        "Fetch Temporal namespace-level summary: namespace config, "
        "retention period, and cluster info. "
        "Attempts to fetch open workflow count via a separate endpoint; "
        "returns None if that endpoint is unavailable. "
        "Use this as a first step to get an overview of Temporal namespace health."
    )
    use_cases = [
        "Getting a high-level overview of a Temporal namespace",
        "Checking namespace retention period and cluster config",
        "Verifying namespace registration state during an incident",
        "Fetching open workflow count as a health signal (falls back to None if unavailable)",
    ]
    requires = ["host"]
    input_schema = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Temporal server hostname."},
            "namespace": {"type": "string", "default": "default", "description": "Temporal namespace to query."},
        },
        "required": ["host"],
    }
    outputs = {
        "namespace": "Namespace name and registration state",
        "retention_days": "Workflow execution retention period",
        "active_cluster": "Active cluster name",
        "open_workflow_count": "Number of currently open workflows (None if unavailable)",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bool(sources.get("temporal", {}).get("connection_verified"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {"host": temporal.get("host", ""), "namespace": temporal.get("namespace", "default")}

    def run(self, host: str, namespace: str = "default", **_kwargs: Any) -> dict[str, Any]:
        if not (host or "").strip():
            return {"source": "temporal", "available": False, "error": "host is required to connect to Temporal.", "namespace": "", "retention_days": "", "active_cluster": "", "open_workflow_count": None}
        try:
            from app.integrations.temporal import TemporalConfig
            client = TemporalClient(TemporalConfig(host=host, namespace=namespace))
            data = client.get_namespace_metrics()
        except TemporalClientError as exc:
            return {"source": "temporal", "available": False, "error": str(exc), "namespace": "", "retention_days": "", "active_cluster": "", "open_workflow_count": None}
        try:
            open_workflow_count: int | None = client.get_workflow_count().get("count", 0)
        except TemporalClientError:
            open_workflow_count = None
        ns_info = data.get("namespaceInfo", {})
        cfg = data.get("config", {})
        replication = data.get("replicationConfig", {})
        return {
            "source": "temporal", "available": True,
            "namespace": ns_info.get("name", ""), "state": ns_info.get("state", ""),
            "description": ns_info.get("description", ""),
            "retention_days": cfg.get("workflowExecutionRetentionTtl", ""),
            "active_cluster": replication.get("activeClusterName", ""),
            "clusters": replication.get("clusters", []),
            "data": ns_info.get("data", {}),
            "open_workflow_count": open_workflow_count,
        }


# Registry auto-discovery: instances must be defined here so __module__ matches
temporal_list_workflows = TemporalListWorkflowsTool()
temporal_workflow_history = TemporalWorkflowHistoryTool()
temporal_task_queue = TemporalTaskQueueTool()
temporal_namespace_metrics = TemporalNamespaceMetricsTool()


def get_temporal_tools() -> list[BaseTool]:
    """Return all Temporal tool instances."""
    return [
        TemporalListWorkflowsTool(),
        TemporalWorkflowHistoryTool(),
        TemporalTaskQueueTool(),
        TemporalNamespaceMetricsTool(),
    ]


__all__ = [
    "TemporalListWorkflowsTool",
    "TemporalWorkflowHistoryTool",
    "TemporalTaskQueueTool",
    "TemporalNamespaceMetricsTool",
    "get_temporal_tools",
    "temporal_list_workflows",
    "temporal_workflow_history",
    "temporal_task_queue",
    "temporal_namespace_metrics",
]
