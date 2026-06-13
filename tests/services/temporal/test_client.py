from typing import Any

from app.services.temporal.client import TemporalClient, TemporalConfig


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
    count_payload = {
        "count": "58",
        "groups": [
            {"groupValues": [{"data": "Running"}], "count": "45"},
            {"groupValues": [{"data": "Failed"}], "count": "8"},
            {"groupValues": [{"data": "TimedOut"}], "count": "5"},
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

    # Verify response shape
    assert response["name"] == "default"
    assert response["state"] == "NAMESPACE_STATE_REGISTERED"
    assert response["workflow_count"] == "58"
    assert len(response["groups"]) == 3


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
