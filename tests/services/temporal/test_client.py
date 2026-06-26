import base64
import json
from typing import Any

from vendors.temporal.client import TemporalClient, TemporalConfig


def _encode_status_payload(status: str) -> str:
    """Encode a status name the way Temporal's HTTP API does: the value is a
    JSON-encoded string, base64-encoded (e.g. "Failed" -> "IkZhaWxlZCI=")."""
    return base64.b64encode(json.dumps(status).encode()).decode()


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)[:200]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


def _client() -> TemporalClient:
    return TemporalClient(TemporalConfig(base_url="http://localhost:7233"))


def error_payload() -> dict[str, Any]:
    return {"code": 5, "message": "Namespace not-a-real-namespace is not found.", "details": []}


def test_temporal_client_is_configured():
    assert _client().is_configured is True


def test_temporal_client_is_not_configured():
    assert TemporalClient(TemporalConfig(base_url="")).is_configured is False
    assert (
        TemporalClient(TemporalConfig(base_url="http://localhost:7233", namespace="")).is_configured
        is False
    )


def test_list_workflow_executions_success(monkeypatch):
    fake_payload = {
        "executions": [
            {
                "execution": {"workflowId": "wf-1", "runId": "run-1"},
                "type": {"name": "MyWorkflowType"},
                "startTime": "2024-01-01T00:00:00Z",
                "closeTime": "2024-01-01T00:05:00Z",
                "status": "WORKFLOW_EXECUTION_STATUS_FAILED",
                "taskQueue": "my-queue",
                "historyLength": "150",
                "historySizeBytes": "8192",
            }
        ],
        "nextPageToken": "",
    }
    temporal = _client()

    captured = []

    def fake_get(url, **kwargs):
        captured.append({"url": url, "params": kwargs.get("params")})
        return _FakeResponse(fake_payload)

    monkeypatch.setattr(temporal._client, "get", fake_get)

    response = temporal.list_workflow_executions()
    assert response["success"] is True

    executions = response["executions"]

    # Verify the right endpoint was hit
    assert captured[0]["url"] == "/api/v1/namespaces/default/workflows"
    assert captured[0]["params"]["pageSize"] == 10

    # verify the response
    assert response["total"] == 1
    assert response["next_page_token"] == ""
    assert executions[0]["type"]["name"] == "MyWorkflowType"
    assert executions[0]["status"] == "WORKFLOW_EXECUTION_STATUS_FAILED"


def test_list_workflow_executions_omits_next_page_token(monkeypatch):
    """The live HTTP API omits nextPageToken when there are no more pages.
    The client must not KeyError — it should default to an empty token."""
    fake_payload = {
        "executions": [
            {
                "execution": {"workflowId": "wf-1", "runId": "run-1"},
                "type": {"name": "MyWorkflowType"},
                "status": "WORKFLOW_EXECUTION_STATUS_FAILED",
            }
        ],
        # nextPageToken intentionally absent — matches a real single-page response.
    }
    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", lambda _url, **_k: _FakeResponse(fake_payload))

    response = temporal.list_workflow_executions()
    assert response["success"] is True
    assert response["total"] == 1
    assert response["next_page_token"] == ""


def test_list_workflow_executions_failure(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        return _FakeResponse(error_payload(), 404)

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.list_workflow_executions()

    assert response["success"] is False


def test_list_workflow_executions_exception(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        raise Exception("unexpected exception")

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.list_workflow_executions()

    assert response["success"] is False
    assert response["error"] == "unexpected exception"


def test_get_workflow_history_success(monkeypatch):
    fake_payload = {
        "history": {
            "events": [
                {
                    "eventId": "1",
                    "eventTime": "2024-01-15T10:00:00Z",
                    "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                    "taskId": "1048576",
                    "workerMayIgnore": False,
                },
                {
                    "eventId": "2",
                    "eventTime": "2024-01-15T10:00:01Z",
                    "eventType": "EVENT_TYPE_WORKFLOW_TASK_SCHEDULED",
                    "taskId": "1048577",
                    "workerMayIgnore": False,
                },
            ]
        },
        "nextPageToken": "",
        "archived": False,
    }

    captured = []

    def fake_get(url, **kwargs) -> _FakeResponse:
        captured.append({"url": url, "params": kwargs.get("params")})
        return _FakeResponse(fake_payload)

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    response = temporal.get_workflow_history("wf-1", "run-1")
    assert response["success"] is True

    # Verify the right endpoint was hit
    assert captured[0]["url"] == "/api/v1/namespaces/default/workflows/wf-1/history"
    assert captured[0]["params"]["pageSize"] == 10
    assert captured[0]["params"]["execution.runId"] == "run-1"

    # verify the response
    assert response["total"] == 2
    assert response["next_page_token"] == ""
    assert response["archived"] is False


def test_get_workflow_history_omits_archived_and_token(monkeypatch):
    """A single-page, non-archived history omits both nextPageToken and
    archived on the wire. The client must default them instead of KeyError-ing."""
    fake_payload = {
        "history": {
            "events": [
                {"eventId": "1", "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"},
            ]
        },
        # nextPageToken and archived intentionally absent.
    }
    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", lambda _url, **_k: _FakeResponse(fake_payload))

    response = temporal.get_workflow_history("wf-1", "run-1")
    assert response["success"] is True
    assert response["total"] == 1
    assert response["next_page_token"] == ""
    assert response["archived"] is False


def test_get_workflow_history_encodes_workflow_id_with_slashes(monkeypatch):
    """Workflow IDs may contain '/' (e.g. 'order-service/order-123'). The client
    must percent-encode it into a single path segment, or the Temporal frontend
    parses the extra segment as a different route and returns 404."""
    captured = []

    def fake_get(url, **_kwargs):
        captured.append(url)
        return _FakeResponse({"history": {"events": []}})

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    temporal.get_workflow_history("order-service/order-123", "run-1")
    assert captured[0] == "/api/v1/namespaces/default/workflows/order-service%2Forder-123/history"


def test_get_workflow_history_failure(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        return _FakeResponse(error_payload(), 404)

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.get_workflow_history("wf-1", "run-1")

    assert response["success"] is False


def test_get_workflow_history_exception(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        raise Exception("unexpected exception")

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.get_workflow_history("wf-1", "run-1")

    assert response["success"] is False
    assert response["error"] == "unexpected exception"


def test_describe_task_queue_success(monkeypatch):
    fake_payload = {
        "pollers": [
            {
                "lastAccessTime": "2024-01-15T10:05:00Z",
                "identity": "worker-1@host-abc",
                "ratePerSecond": 100.0,
            },
            {
                "lastAccessTime": "2024-01-15T10:04:55Z",
                "identity": "worker-2@host-def",
                "ratePerSecond": 100.0,
            },
        ],
        "stats": {
            "approximateBacklogCount": "42",
            "approximateBacklogAge": "30.5s",
            "tasksAddRate": 5.2,
            "tasksDispatchRate": 4.8,
        },
    }
    captured = []

    def fake_get(url, **kwargs) -> _FakeResponse:
        captured.append({"url": url, "params": kwargs.get("params")})
        return _FakeResponse(fake_payload)

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    response = temporal.describe_task_queue("my-queue")
    assert response["success"] is True
    assert response["stats"]["approximateBacklogCount"] == "42"
    assert response["total"] == 2

    # Verify the right endpoint was hit
    assert captured[0]["url"] == "/api/v1/namespaces/default/task-queues/my-queue"
    assert captured[0]["params"]["reportStats"] is True
    assert captured[0]["params"]["taskQueueType"] == "TASK_QUEUE_TYPE_WORKFLOW"


def test_describe_task_queue_omits_pollers(monkeypatch):
    """When no worker is polling, the live API omits the pollers array entirely.
    The client must default to [] (total 0) rather than KeyError."""
    fake_payload = {
        "stats": {"approximateBacklogAge": "0s"},
        # pollers intentionally absent — no active workers right now.
    }
    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", lambda _url, **_k: _FakeResponse(fake_payload))

    response = temporal.describe_task_queue("idle-queue")
    assert response["success"] is True
    assert response["pollers"] == []
    assert response["total"] == 0


def test_describe_task_queue_encodes_name_with_slashes(monkeypatch):
    """Task queue names are free-form strings and may contain '/'. Encode them
    into a single path segment so the request hits the right route."""
    captured = []

    def fake_get(url, **_kwargs):
        captured.append(url)
        return _FakeResponse({"stats": {}})

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    temporal.describe_task_queue("payments/charge-queue")
    assert captured[0] == "/api/v1/namespaces/default/task-queues/payments%2Fcharge-queue"


def test_describe_task_queue_failure(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        return _FakeResponse(error_payload(), 404)

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.describe_task_queue("my-queue")

    assert response["success"] is False


def test_describe_task_queue_exception(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        raise Exception("unexpected exception")

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.describe_task_queue("my-queue")

    assert response["success"] is False
    assert response["error"] == "unexpected exception"


def test_get_namespace_info_success(monkeypatch):
    ns_payload = {
        "namespaceInfo": {
            "name": "default",
            "state": "NAMESPACE_STATE_REGISTERED",
            "description": "Default namespace",
            "ownerEmail": "team@example.com",
            "id": "ns-id-123",
        },
        "config": {},
        "isGlobalNamespace": False,
    }
    # The real HTTP API base64-encodes each status name as a Temporal Payload.
    count_payload = {
        "count": "58",
        "groups": [
            {"groupValues": [{"data": _encode_status_payload("Running")}], "count": "45"},
            {"groupValues": [{"data": _encode_status_payload("Failed")}], "count": "8"},
            {"groupValues": [{"data": _encode_status_payload("TimedOut")}], "count": "5"},
        ],
    }

    captured = []

    def fake_get(url, **kwargs):
        captured.append({"url": url, "params": kwargs.get("params")})
        if "workflow-count" in url:
            return _FakeResponse(count_payload)
        return _FakeResponse(ns_payload)

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    response = temporal.get_namespace_info()
    assert response["success"] is True

    # Verify correct endpoints were hit
    assert captured[0]["url"] == "/api/v1/namespaces/default"
    assert captured[1]["url"] == "/api/v1/namespaces/default/workflow-count"
    assert captured[1]["params"]["query"] == "GROUP BY ExecutionStatus"

    # Verify response shape: groups are decoded from base64 and flattened to
    # [{"status", "count"}] — the LLM never sees raw Payload encoding.
    assert response["name"] == "default"
    assert response["state"] == "NAMESPACE_STATE_REGISTERED"
    assert response["workflow_count"] == "58"
    assert response["groups"] == [
        {"status": "Running", "count": "45"},
        {"status": "Failed", "count": "8"},
        {"status": "TimedOut", "count": "5"},
    ]


def test_get_namespace_info_failure(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        return _FakeResponse(error_payload(), 404)

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.get_namespace_info()

    assert response["success"] is False


def test_get_namespace_info_exception(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        raise Exception("connection refused")

    monkeypatch.setattr(temporal._client, "get", fake_get)
    response = temporal.get_namespace_info()

    assert response["success"] is False
    assert response["error"] == "connection refused"


def test_probe_access_success(monkeypatch):
    payload = {
        "namespaceInfo": {
            "name": "default",
            "state": "NAMESPACE_STATE_REGISTERED",
            "description": "Default namespace",
            "ownerEmail": "team@example.com",
            "id": "ns-id-123",
        },
        "config": {},
        "isGlobalNamespace": False,
    }

    def fake_get(_url, **_kwargs):
        return _FakeResponse(payload)

    temporal = _client()
    monkeypatch.setattr(temporal._client, "get", fake_get)

    result = temporal.probe_access()

    assert result.ok is True


def test_probe_access_failure(monkeypatch):
    temporal = _client()

    def fake_get(_url, **_kwargs):
        return _FakeResponse(error_payload(), 404)

    monkeypatch.setattr(temporal._client, "get", fake_get)
    result = temporal.probe_access()

    assert result.ok is False


def test_probe_access_on_unconfigured_client(monkeypatch):
    temporal = TemporalClient(TemporalConfig(base_url="", namespace=""))
    result = temporal.probe_access()
    assert result.ok is False
