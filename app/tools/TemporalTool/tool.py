"""Temporal LangChain tool definitions.

Four tools surface the four Temporal API capabilities:
  1. TemporalListWorkflowsTool        — list recent executions with status/failure reason
  2. TemporalWorkflowHistoryTool      — fetch event history for a single run
  3. TemporalTaskQueueTool            — list task queues and worker pollers
  4. TemporalNamespaceMetricsTool     — namespace-level metrics (open workflows, errors)
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.integrations.temporal import (
    TemporalConfig,
    load_temporal_config_from_env,
    load_temporal_config_from_integration,
)
from app.services.temporal.client import TemporalClient, TemporalClientError

logger = logging.getLogger(__name__)


# Input schemas

class ListWorkflowsInput(BaseModel):
    """Input schema for listing Temporal workflow executions."""

    query: str = Field(
        default="",
        description=(
            "Temporal visibility query string. Examples: "
            "``ExecutionStatus='Failed'``, "
            "``WorkflowType='OrderWorkflow' AND ExecutionStatus='Running'``. "
            "Leave empty to list all recent executions."
        ),
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of workflow executions to return (1–50).",
    )


class WorkflowHistoryInput(BaseModel):
    """Input schema for fetching a Temporal workflow's event history."""

    workflow_id: str = Field(
        description="The workflow ID, e.g. ``order-workflow-abc123``."
    )
    run_id: str = Field(
        description="The run ID of the specific execution to inspect."
    )
    max_event_count: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of history events to return (1–200).",
    )


class TaskQueueInput(BaseModel):
    """Input schema for fetching Temporal task queue info."""

    task_queue: str = Field(
        description="Name of the task queue, e.g. ``payment-task-queue``."
    )


class NamespaceMetricsInput(BaseModel):
    """Input schema for fetching Temporal namespace metrics (no parameters needed)."""

    pass


#Implementation of Tools

def _make_client(config: TemporalConfig | None = None) -> TemporalClient:
    return TemporalClient(config or load_temporal_config_from_env())

class TemporalListWorkflowsTool(BaseTool):
    """List recent Temporal workflow executions with status and failure reason."""

    name: str = "temporal_list_workflows"
    description: str = (
        "List recent Temporal workflow executions. "
        "Use this to identify failed, timed-out, or stuck workflows in a namespace. "
        "Supports Temporal visibility queries to filter by status, type, or time range."
    )
    args_schema: type[BaseModel] = ListWorkflowsInput
    config: TemporalConfig | None = None

    def _run(self, query: str = "", page_size: int = 20) -> str:
        client = _make_client(self.config)
        try:
            executions = client.list_workflows(query=query, page_size=page_size)
        except TemporalClientError as exc:
            return f"Error fetching workflows: {exc}"

        if not executions:
            return "No workflow executions found matching the query."

        results = []
        for ex in executions:
            execution = ex.get("execution", {})
            status = ex.get("status", "UNKNOWN")
            wf_type = ex.get("type", {}).get("name", "unknown")
            start_time = ex.get("startTime", "")
            close_time = ex.get("closeTime", "")

            entry: dict = {
                "workflow_id": execution.get("workflowId", ""),
                "run_id": execution.get("runId", ""),
                "type": wf_type,
                "status": status,
                "start_time": start_time,
            }
            if close_time:
                entry["close_time"] = close_time

            # Surface failure reason if present
            if status in ("FAILED", "TIMED_OUT", "TERMINATED"):
                entry["hint"] = "Use temporal_workflow_history to fetch the failure reason and stack trace."

            results.append(entry)

        return json.dumps(results, indent=2)

    async def _arun(self, query: str = "", page_size: int = 20) -> str:
        return self._run(query=query, page_size=page_size)


class TemporalWorkflowHistoryTool(BaseTool):
    """Fetch the event history for a specific Temporal workflow run."""

    name: str = "temporal_workflow_history"
    description: str = (
        "Fetch the full event history for a specific Temporal workflow execution. "
        "Use this to diagnose failures: the history shows every activity attempt, "
        "retry, timeout, signal, and the final failure cause with stack traces."
    )
    args_schema: type[BaseModel] = WorkflowHistoryInput
    config: TemporalConfig | None = None

    def _run(
        self,
        workflow_id: str,
        run_id: str,
        max_event_count: int = 50,
    ) -> str:
        client = _make_client(self.config)
        try:
            events = client.get_workflow_history(
                workflow_id=workflow_id,
                run_id=run_id,
                max_event_count=max_event_count,
            )
        except TemporalClientError as exc:
            return f"Error fetching workflow history: {exc}"

        if not events:
            return f"No history events found for workflow {workflow_id}/{run_id}."

        # Summarise key failure events for the agent
        summary = []
        for event in events:
            event_type = event.get("eventType", "")
            event_id = event.get("eventId", "")
            event_time = event.get("eventTime", "")
            attrs_key = _attrs_key(event_type)
            attrs = event.get(attrs_key, {})

            entry: dict = {
                "event_id": event_id,
                "event_time": event_time,
                "event_type": event_type,
            }

            # Pull out failure details when present
            failure = attrs.get("failure")
            if failure:
                entry["failure"] = {
                    "message": failure.get("message", ""),
                    "cause": failure.get("cause", {}).get("message", ""),
                    "stack_trace": failure.get("stackTrace", "")[:500],
                }

            # Pull out activity info when present
            if "activityType" in attrs:
                entry["activity_type"] = attrs["activityType"].get("name", "")
            if "attempt" in attrs:
                entry["attempt"] = attrs["attempt"]

            summary.append(entry)

        return json.dumps(summary, indent=2)

    async def _arun(self, workflow_id: str, run_id: str, max_event_count: int = 50) -> str:
        return self._run(workflow_id=workflow_id, run_id=run_id, max_event_count=max_event_count)


class TemporalTaskQueueTool(BaseTool):
    """List task queue pollers and worker health for a Temporal task queue."""

    name: str = "temporal_task_queue"
    description: str = (
        "Fetch Temporal task queue info: which workers are polling, their identity, "
        "last access time, and whether the queue is drained. "
        "Use this when investigating worker crashes, missing workers, or stuck workflows."
    )
    args_schema: type[BaseModel] = TaskQueueInput
    config: TemporalConfig | None = None

    def _run(self, task_queue: str) -> str:
        client = _make_client(self.config)
        try:
            data = client.list_task_queues(task_queue=task_queue)
        except TemporalClientError as exc:
            return f"Error fetching task queue '{task_queue}': {exc}"

        pollers = data.get("pollers", [])
        status = data.get("taskQueueStatus", {})

        result: dict = {
            "task_queue": task_queue,
            "poller_count": len(pollers),
            "pollers": [
                {
                    "identity": p.get("identity", ""),
                    "last_access_time": p.get("lastAccessTime", ""),
                    "rate_per_second": p.get("ratePerSecond", 0),
                }
                for p in pollers
            ],
            "backlog_count": status.get("backlogCountHint", 0),
            "read_level": status.get("readLevel", 0),
        }

        if not pollers:
            result["warning"] = (
                "No workers are currently polling this task queue. "
                "Workflows may be stuck waiting for workers."
            )

        return json.dumps(result, indent=2)

    async def _arun(self, task_queue: str) -> str:
        return self._run(task_queue=task_queue)


class TemporalNamespaceMetricsTool(BaseTool):
    """Fetch namespace-level Temporal metrics: open workflows, activity errors."""

    name: str = "temporal_namespace_metrics"
    description: str = (
        "Fetch Temporal namespace-level summary metrics: open workflow count, "
        "namespace config, retention period, and cluster info. "
        "Use this as a first step to get an overview of Temporal health."
    )
    args_schema: type[BaseModel] = NamespaceMetricsInput
    config: TemporalConfig | None = None

    def _run(self) -> str:
        client = _make_client(self.config)
        try:
            data = client.get_namespace_metrics()
            count_data = client.get_workflow_count()
        except TemporalClientError as exc:
            return f"Error fetching namespace metrics: {exc}"

        ns_info = data.get("namespaceInfo", {})
        config = data.get("config", {})
        replication = data.get("replicationConfig", {})

        result = {
            "namespace": ns_info.get("name", ""),
            "state": ns_info.get("state", ""),
            "description": ns_info.get("description", ""),
            "retention_days": config.get("workflowExecutionRetentionTtl", ""),
            "active_cluster": replication.get("activeClusterName", ""),
            "clusters": replication.get("clusters", []),
            "data": ns_info.get("data", {}),
            "open_workflow_count": count_data.get("count" , 0),
        }

        return json.dumps(result, indent=2)

    async def _arun(self) -> str:
        return self._run()


#Factory

def get_temporal_tools(
    config: TemporalConfig | None = None,
    integration_config: object | None = None,
) -> list[BaseTool]:
    """Return all Temporal tools wired to the given config.

    Priority:
        1. Explicit TemporalConfig passed directly
        2. TemporalIntegrationConfig from the registry
        3. Environment variables fallback
    """
    if config is not None:
        resolved = config
    elif integration_config is not None:
        from app.integrations.config_models import TemporalIntegrationConfig
        if isinstance(integration_config, TemporalIntegrationConfig):
            resolved = load_temporal_config_from_integration(integration_config)
        else:
            resolved = load_temporal_config_from_env()
    else:
        resolved = load_temporal_config_from_env()

    return [
        TemporalListWorkflowsTool(config=resolved),
        TemporalWorkflowHistoryTool(config=resolved),
        TemporalTaskQueueTool(config=resolved),
        TemporalNamespaceMetricsTool(config=resolved),
    ]
# Helpers


def _attrs_key(event_type: str) -> str:
    """Map event type string to its attributes dict key in the Temporal API response."""
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
