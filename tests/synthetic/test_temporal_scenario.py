"""Synthetic RCA scenario using Temporal as the evidence source.

Validates that the full tool → client → HTTP stack surfaces realistic
Temporal API responses for a workflow failure investigation scenario.

Scenario: A payment processing workflow has failed because its
ChargePaymentMethod activity hit a non-retryable PaymentGatewayError
(HTTP 401 from the charge service). The agent uses namespace info to see
elevated failure counts, lists failed workflows, drills into the event
history to find the failed activity and its nested failure cause, and checks
the task queue for worker health.

The fixtures mirror the *real* Temporal HTTP API wire shapes captured from a
live self-hosted dev server: status names arrive base64-encoded,
and falsy fields like nextPageToken / archived / pollers are omitted entirely.

The fixture Temporal instance is implemented as an httpx.MockTransport
returning canned JSON for each API path.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_SEED_TOOL_SOURCES as _SEEDING_MAP,
)
from core.domain.alerts.alert_source import (
    ALERT_SOURCE_TO_TOOL_SOURCES as _PROMPT_MAP,
)
from integrations.temporal.client import TemporalClient, TemporalConfig
from integrations.temporal.tools import (
    TemporalNamespaceInfoTool,
    TemporalTaskQueueTool,
    TemporalWorkflowHistoryTool,
    TemporalWorkflowsTool,
)

pytestmark = pytest.mark.synthetic


# --- fixture Temporal instance ------------------------------------------------

_NAMESPACE = "production"
_FAILED_WORKFLOW_ID = "payment-proc-abc123"
_FAILED_RUN_ID = "run-def456"
_TASK_QUEUE = "payment-queue"


def _encode_status_payload(status: str) -> str:
    """Encode a status name as Temporal's HTTP API does: JSON-encoded string,
    base64-encoded (e.g. "Failed" -> "IkZhaWxlZCI=")."""
    return base64.b64encode(json.dumps(status).encode()).decode()


# DescribeNamespace response
_FIXTURE_DESCRIBE_NAMESPACE = {
    "namespaceInfo": {
        "name": _NAMESPACE,
        "state": "NAMESPACE_STATE_REGISTERED",
        "description": "Production payment workflows",
    },
    "config": {
        "workflowExecutionRetentionTtl": "259200s",
    },
    "replicationConfig": {
        "activeClusterName": "us-east-1",
    },
}

# CountWorkflowExecutions with GROUP BY ExecutionStatus.
# Status names arrive base64-encoded as Temporal Payloads (real wire shape).
_FIXTURE_COUNT_WORKFLOWS = {
    "count": "142",
    "groups": [
        {"groupValues": [{"data": _encode_status_payload("Running")}], "count": "95"},
        {"groupValues": [{"data": _encode_status_payload("Failed")}], "count": "38"},
        {"groupValues": [{"data": _encode_status_payload("TimedOut")}], "count": "9"},
    ],
}

# ListWorkflowExecutions response — includes the failed payment workflow
_FIXTURE_LIST_WORKFLOWS = {
    "executions": [
        {
            "execution": {
                "workflowId": _FAILED_WORKFLOW_ID,
                "runId": _FAILED_RUN_ID,
            },
            "type": {"name": "PaymentProcessingWorkflow"},
            "startTime": "2024-01-15T10:00:00Z",
            "closeTime": "2024-01-15T10:02:30Z",
            "status": "WORKFLOW_EXECUTION_STATUS_FAILED",
            "taskQueue": _TASK_QUEUE,
            "historyLength": "12",
            "historySizeBytes": "4096",
        },
        {
            "execution": {
                "workflowId": "order-fulfill-xyz789",
                "runId": "run-ghi012",
            },
            "type": {"name": "OrderFulfillmentWorkflow"},
            "startTime": "2024-01-15T09:55:00Z",
            "closeTime": "2024-01-15T10:01:00Z",
            "status": "WORKFLOW_EXECUTION_STATUS_TIMED_OUT",
            "taskQueue": _TASK_QUEUE,
            "historyLength": "8",
            "historySizeBytes": "2048",
        },
    ],
    # nextPageToken omitted — matches a real single-page response.
}

# GetWorkflowHistory for the failed payment workflow.
# Mirrors the real wire shape: a non-retryable activity failure surfaces as a
# nested failure.cause chain (Go SDK), terminating in WORKFLOW_EXECUTION_FAILED.
# There is no ACTIVITY_TASK_TIMED_OUT event — a non-retryable error fails fast.
_FIXTURE_WORKFLOW_HISTORY = {
    "history": {
        "events": [
            {
                "eventId": "1",
                "eventTime": "2024-01-15T10:00:00Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                "taskId": "1048576",
                "workerMayIgnore": False,
                "workflowExecutionStartedEventAttributes": {
                    "workflowType": {"name": "PaymentProcessingWorkflow"},
                    "taskQueue": {"name": _TASK_QUEUE},
                },
            },
            {
                "eventId": "5",
                "eventTime": "2024-01-15T10:00:01Z",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
                "taskId": "1048580",
                "workerMayIgnore": False,
                "activityTaskScheduledEventAttributes": {
                    "activityType": {"name": "ChargePaymentMethod"},
                    "taskQueue": {"name": _TASK_QUEUE},
                },
            },
            {
                "eventId": "11",
                "eventTime": "2024-01-15T10:00:02Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "taskId": "1048590",
                "workerMayIgnore": False,
                "workflowExecutionFailedEventAttributes": {
                    "failure": {
                        "message": "activity error",
                        "source": "GoSDK",
                        "cause": {
                            "message": (
                                "payment gateway rejected charge: downstream credentials invalid"
                            ),
                            "source": "GoSDK",
                            "cause": {
                                "message": "HTTP 401 from charge-service",
                                "source": "GoSDK",
                                "applicationFailureInfo": {},
                            },
                            "applicationFailureInfo": {
                                "type": "PaymentGatewayError",
                                "nonRetryable": True,
                            },
                        },
                        "activityFailureInfo": {
                            "scheduledEventId": "5",
                            "startedEventId": "6",
                            "activityType": {"name": "ChargePaymentMethod"},
                            "activityId": "5",
                            "retryState": "RETRY_STATE_NON_RETRYABLE_FAILURE",
                        },
                    },
                    "retryState": "RETRY_STATE_RETRY_POLICY_NOT_SET",
                    "workflowTaskCompletedEventId": "10",
                },
            },
        ]
    },
    # nextPageToken and archived omitted — matches a real single-page history.
}

# DescribeTaskQueue — workers are polling but backlog is growing
_FIXTURE_DESCRIBE_TASK_QUEUE = {
    "pollers": [
        {
            "lastAccessTime": "2024-01-15T10:05:00Z",
            "identity": "worker-1@payment-host-a",
            "ratePerSecond": 50.0,
        },
    ],
    "stats": {
        "approximateBacklogCount": "156",
        "approximateBacklogAge": "120.5s",
        "tasksAddRate": 12.3,
        "tasksDispatchRate": 4.1,
    },
}


def _make_mock_temporal_transport() -> httpx.MockTransport:
    """Build an httpx.MockTransport that routes Temporal HTTP API requests
    to the correct canned response based on URL path."""

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        # CountWorkflowExecutions (checked before DescribeNamespace since it is
        # a more specific path under the same namespace prefix)
        if path == f"/api/v1/namespaces/{_NAMESPACE}/workflow-count":
            return httpx.Response(200, json=_FIXTURE_COUNT_WORKFLOWS)

        # DescribeNamespace
        if path == f"/api/v1/namespaces/{_NAMESPACE}":
            return httpx.Response(200, json=_FIXTURE_DESCRIBE_NAMESPACE)

        # GetWorkflowHistory (more specific than ListWorkflowExecutions)
        if f"/workflows/{_FAILED_WORKFLOW_ID}/history" in path:
            return httpx.Response(200, json=_FIXTURE_WORKFLOW_HISTORY)

        # ListWorkflowExecutions
        if path == f"/api/v1/namespaces/{_NAMESPACE}/workflows":
            return httpx.Response(200, json=_FIXTURE_LIST_WORKFLOWS)

        # DescribeTaskQueue
        if f"/task-queues/{_TASK_QUEUE}" in path:
            return httpx.Response(200, json=_FIXTURE_DESCRIBE_TASK_QUEUE)

        return httpx.Response(404, json={"message": f"no fixture for path: {path}"})

    return httpx.MockTransport(_handler)


@pytest.fixture
def patched_temporal_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch TemporalClient at each tool's import location so tool.run()
    exercises the full stack through a fixture HTTP transport."""
    mock_transport = _make_mock_temporal_transport()

    def _make_patched_client(_config: Any) -> TemporalClient:
        """Create a TemporalClient with a mocked httpx transport."""
        config = TemporalConfig(
            base_url="http://temporal.example.com:7233",
            namespace=_NAMESPACE,
            api_key="test-key",
        )
        client = TemporalClient(config)
        # Replace the internal httpx.Client with one using our mock transport
        client._client = httpx.Client(
            base_url="http://temporal.example.com:7233",
            headers={"Authorization": "Bearer test-key"},
            timeout=10.0,
            transport=mock_transport,
        )
        return client

    monkeypatch.setattr("integrations.temporal.tools.TemporalClient", _make_patched_client)
    monkeypatch.setattr("integrations.temporal.tools.TemporalClient", _make_patched_client)
    monkeypatch.setattr("integrations.temporal.tools.TemporalClient", _make_patched_client)
    monkeypatch.setattr("integrations.temporal.tools.TemporalClient", _make_patched_client)


# --- alert source mapping -----------------------------------------------------


def test_temporal_alert_source_seeds_temporal_tools() -> None:
    """A temporal-sourced alert pre-seeds temporal tools before the ReAct loop."""
    assert "temporal" in _SEEDING_MAP
    assert _SEEDING_MAP["temporal"] == ("temporal",)


def test_temporal_alert_source_appears_in_prompt_map() -> None:
    """A temporal-sourced alert is treated as a primary temporal-tool source in the prompt."""
    assert "temporal" in _PROMPT_MAP
    assert _PROMPT_MAP["temporal"] == ("temporal",)


# --- synthetic investigation scenario ----------------------------------------


def test_namespace_info_shows_elevated_failures(
    patched_temporal_client: None,
) -> None:
    """Namespace info surfaces workflow counts grouped by status,
    revealing elevated Failed and TimedOut counts."""
    tool = TemporalNamespaceInfoTool()
    result = tool.run(
        base_url="http://temporal.example.com:7233",
        namespace=_NAMESPACE,
    )
    assert result["available"] is True
    assert result["name"] == _NAMESPACE
    assert result["state"] == "NAMESPACE_STATE_REGISTERED"

    # Verify the agent can see failure breakdown — groups are decoded from
    # base64 and flattened to {status, count} by the client.
    groups = result["groups"]
    failed_groups = [g for g in groups if g["status"] == "Failed"]
    assert len(failed_groups) == 1
    assert failed_groups[0]["count"] == "38"


def test_list_workflows_surfaces_failed_executions(
    patched_temporal_client: None,
) -> None:
    """Listing workflows reveals the failed PaymentProcessingWorkflow
    with its task queue for further investigation."""
    tool = TemporalWorkflowsTool()
    result = tool.run(
        base_url="http://temporal.example.com:7233",
        namespace=_NAMESPACE,
    )
    assert result["available"] is True
    assert result["total"] == 2

    executions = result["executions"]
    failed = [e for e in executions if "FAILED" in e.get("status", "")]
    assert len(failed) == 1
    assert failed[0]["execution"]["workflowId"] == _FAILED_WORKFLOW_ID
    assert failed[0]["type"]["name"] == "PaymentProcessingWorkflow"
    assert failed[0]["taskQueue"] == _TASK_QUEUE


def test_workflow_history_reveals_nonretryable_payment_failure(
    patched_temporal_client: None,
) -> None:
    """Event history for the failed workflow shows the ChargePaymentMethod
    activity failed with a non-retryable PaymentGatewayError, and the nested
    failure cause chain pins the root cause to an HTTP 401 from charge-service."""
    tool = TemporalWorkflowHistoryTool()
    result = tool.run(
        base_url="http://temporal.example.com:7233",
        workflow_id=_FAILED_WORKFLOW_ID,
        run_id=_FAILED_RUN_ID,
        namespace=_NAMESPACE,
    )
    assert result["available"] is True
    assert result["total"] >= 3

    events = result["events"]

    # The activity that failed is identified on the schedule event.
    scheduled = [e for e in events if e["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"]
    assert len(scheduled) == 1
    assert (
        scheduled[0]["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        == "ChargePaymentMethod"
    )

    # The workflow failure carries the nested cause chain (Go SDK shape):
    #   "activity error" -> gateway rejection -> HTTP 401 root cause.
    wf_failed = [e for e in events if e["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED"]
    assert len(wf_failed) == 1
    failure = wf_failed[0]["workflowExecutionFailedEventAttributes"]["failure"]

    # The failing activity is named on activityFailureInfo.
    assert failure["activityFailureInfo"]["activityType"]["name"] == "ChargePaymentMethod"
    assert failure["activityFailureInfo"]["retryState"] == "RETRY_STATE_NON_RETRYABLE_FAILURE"

    # First-level cause: the application error and its non-retryable type.
    cause = failure["cause"]
    assert "payment gateway rejected charge" in cause["message"]
    assert cause["applicationFailureInfo"]["type"] == "PaymentGatewayError"
    assert cause["applicationFailureInfo"]["nonRetryable"] is True

    # Root cause: the underlying HTTP 401 from the downstream charge service.
    assert "HTTP 401 from charge-service" in cause["cause"]["message"]


def test_task_queue_shows_growing_backlog(
    patched_temporal_client: None,
) -> None:
    """Task queue inspection reveals workers are active but the backlog
    is growing — add rate (12.3/s) far exceeds dispatch rate (4.1/s)."""
    tool = TemporalTaskQueueTool()
    result = tool.run(
        base_url="http://temporal.example.com:7233",
        task_queue_name=_TASK_QUEUE,
        namespace=_NAMESPACE,
    )
    assert result["available"] is True

    # Workers are polling (not dead)
    assert result["total"] >= 1
    assert result["pollers"][0]["identity"] == "worker-1@payment-host-a"

    # But backlog is growing — evidence of throughput problem
    stats = result["stats"]
    assert int(stats["approximateBacklogCount"]) > 100
    assert float(stats["tasksAddRate"]) > float(stats["tasksDispatchRate"])
