"""Synthetic RCA scenario using Temporal as the evidence source.

This test validates that Temporal tools return realistic fixture data
and that the investigation agent can surface workflow failures without
a live Temporal server.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.integrations.registry import INTEGRATION_SPECS_BY_SERVICE
from app.tools.TemporalTool.tool import (
    TemporalListWorkflowsTool,
    TemporalNamespaceMetricsTool,
    TemporalTaskQueueTool,
    TemporalWorkflowHistoryTool,
)


class _FixtureTemporalBackend:
    """Minimal fixture backend for synthetic Temporal scenarios."""

    def list_workflows(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "execution": {"workflowId": "order-workflow-abc123", "runId": "run-001"},
                "type": {"name": "OrderWorkflow"},
                "status": "FAILED",
                "startTime": "2024-01-15T10:00:00Z",
                "closeTime": "2024-01-15T10:01:05Z",
            },
            {
                "execution": {"workflowId": "order-workflow-def456", "runId": "run-002"},
                "type": {"name": "OrderWorkflow"},
                "status": "TIMED_OUT",
                "startTime": "2024-01-15T10:02:00Z",
                "closeTime": "2024-01-15T10:03:00Z",
            },
            {
                "execution": {"workflowId": "order-workflow-ghi789", "runId": "run-003"},
                "type": {"name": "OrderWorkflow"},
                "status": "RUNNING",
                "startTime": "2024-01-15T10:05:00Z",
            },
        ]

    def get_workflow_history(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "eventId": "1",
                "eventTime": "2024-01-15T10:00:00Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
            },
            {
                "eventId": "2",
                "eventTime": "2024-01-15T10:00:01Z",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
                "activityTaskScheduledEventAttributes": {
                    "activityType": {"name": "ProcessPayment"},
                },
            },
            {
                "eventId": "3",
                "eventTime": "2024-01-15T10:01:05Z",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT",
                "activityTaskTimedOutEventAttributes": {
                    "failure": {
                        "message": "activity timeout after 60s",
                        "cause": {"message": "deadline exceeded"},
                        "stackTrace": "at ProcessPaymentActivity.java:42",
                    },
                    "attempt": 3,
                },
            },
            {
                "eventId": "4",
                "eventTime": "2024-01-15T10:01:05Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "workflowExecutionFailedEventAttributes": {
                    "failure": {
                        "message": "activity timeout after 60s",
                        "stackTrace": "at OrderWorkflow.java:99",
                    }
                },
            },
        ]

    def list_task_queues(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "pollers": [],
            "taskQueueStatus": {"backlogCountHint": 47, "readLevel": 10},
        }

    def get_namespace_metrics(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "namespaceInfo": {
                "name": "default",
                "state": "Registered",
                "description": "Default namespace",
            },
            "config": {"workflowExecutionRetentionTtl": "72h"},
            "replicationConfig": {
                "activeClusterName": "us-east",
                "clusters": [{"clusterName": "us-east"}],
            },
        }

    def get_workflow_count(self, **_kwargs: Any) -> dict[str, Any]:
        return {"count": 47}


def test_temporal_registered_in_integration_registry() -> None:
    """Temporal is registered in the integration registry."""
    assert "temporal" in INTEGRATION_SPECS_BY_SERVICE


def test_temporal_list_workflows_synthetic_scenario() -> None:
    """A synthetic Temporal alert surfaces failed workflow executions."""
    backend = _FixtureTemporalBackend()
    mock_client = MagicMock()
    mock_client.list_workflows.side_effect = lambda **kw: backend.list_workflows(**kw)

    with patch("app.tools.TemporalTool.tool.TemporalClient", return_value=mock_client):
        tool = TemporalListWorkflowsTool()
        result = tool.run(
            host="localhost",
            namespace="default",
            query="ExecutionStatus='Failed'",
        )

    assert result["available"] is True
    assert result["total"] == 3
    assert result["total_failed"] == 2
    failed_ids = [ex["execution"]["workflowId"] for ex in result["failed_executions"]]
    assert "order-workflow-abc123" in failed_ids
    assert "order-workflow-def456" in failed_ids


def test_temporal_workflow_history_synthetic_scenario() -> None:
    """Workflow history surfaces the activity timeout as the root cause."""
    backend = _FixtureTemporalBackend()
    mock_client = MagicMock()
    mock_client.get_workflow_history.side_effect = (
        lambda **kw: backend.get_workflow_history(**kw)
    )

    with patch("app.tools.TemporalTool.tool.TemporalClient", return_value=mock_client):
        tool = TemporalWorkflowHistoryTool()
        result = tool.run(
            host="localhost",
            namespace="default",
            workflow_id="order-workflow-abc123",
            run_id="run-001",
        )

    assert result["available"] is True
    assert result["total_events"] == 4
    assert result["total_failure_events"] == 2
    failure_types = [ev["eventType"] for ev in result["failure_events"]]
    assert "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT" in failure_types
    assert "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED" in failure_types


def test_temporal_task_queue_synthetic_scenario() -> None:
    """Task queue with no pollers surfaces a warning about missing workers."""
    backend = _FixtureTemporalBackend()
    mock_client = MagicMock()
    mock_client.list_task_queues.side_effect = (
        lambda **kw: backend.list_task_queues(**kw)
    )

    with patch("app.tools.TemporalTool.tool.TemporalClient", return_value=mock_client):
        tool = TemporalTaskQueueTool()
        result = tool.run(
            host="localhost",
            namespace="default",
            task_queue="payment-task-queue",
        )

    assert result["available"] is True
    assert result["poller_count"] == 0
    assert "warning" in result
    assert len(result["unhealthy_queues"]) == 1
    assert result["backlog_count"] == 47


def test_temporal_namespace_metrics_synthetic_scenario() -> None:
    """Namespace metrics returns cluster info and open workflow count."""
    backend = _FixtureTemporalBackend()
    mock_client = MagicMock()
    mock_client.get_namespace_metrics.side_effect = (
        lambda **kw: backend.get_namespace_metrics(**kw)
    )
    mock_client.get_workflow_count.side_effect = (
        lambda **kw: backend.get_workflow_count(**kw)
    )

    with patch("app.tools.TemporalTool.tool.TemporalClient", return_value=mock_client):
        tool = TemporalNamespaceMetricsTool()
        result = tool.run(host="localhost", namespace="default")

    assert result["available"] is True
    assert result["namespace"] == "default"
    assert result["active_cluster"] == "us-east"
    assert result["retention_days"] == "72h"
    assert result["open_workflow_count"] == 47


def test_temporal_full_rca_scenario() -> None:
    """Full synthetic RCA: list failed workflows → get history → check task queue."""
    backend = _FixtureTemporalBackend()
    mock_client = MagicMock()
    mock_client.list_workflows.side_effect = lambda **kw: backend.list_workflows(**kw)
    mock_client.get_workflow_history.side_effect = (
        lambda **kw: backend.get_workflow_history(**kw)
    )
    mock_client.list_task_queues.side_effect = (
        lambda **kw: backend.list_task_queues(**kw)
    )

    with patch("app.tools.TemporalTool.tool.TemporalClient", return_value=mock_client):
        # Step 1: list failed workflows
        workflows_result = TemporalListWorkflowsTool().run(
            host="localhost",
            query="ExecutionStatus='Failed'",
        )
        assert workflows_result["total_failed"] == 2

        # Step 2: get history for first failed workflow
        first_failed = workflows_result["failed_executions"][0]
        history_result = TemporalWorkflowHistoryTool().run(
            host="localhost",
            workflow_id=first_failed["execution"]["workflowId"],
            run_id=first_failed["execution"]["runId"],
        )
        assert history_result["total_failure_events"] >= 1

        # Step 3: check task queue
        queue_result = TemporalTaskQueueTool().run(
            host="localhost",
            task_queue="payment-task-queue",
        )
        assert queue_result["poller_count"] == 0
        assert "warning" in queue_result
