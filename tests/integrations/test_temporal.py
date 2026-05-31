"""Unit tests for the Temporal integration.

All tests use mocks — no live Temporal server required.
Run with: pytest tests/integrations/test_temporal.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.temporal import TemporalConfig, load_temporal_config_from_env
from app.services.temporal.client import TemporalClient, TemporalClientError
from app.tools.TemporalTool.tool import (
    TemporalListWorkflowsTool,
    TemporalNamespaceMetricsTool,
    TemporalTaskQueueTool,
    TemporalWorkflowHistoryTool,
    get_temporal_tools,
)

# Fixtures

@pytest.fixture()
def config() -> TemporalConfig:
    return TemporalConfig(host="localhost", port=7233, namespace="default")


@pytest.fixture()
def client(config: TemporalConfig) -> TemporalClient:
    return TemporalClient(config)


# TemporalConfig

class TestTemporalConfig:
    def test_default_values(self) -> None:
        cfg = TemporalConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 7233
        assert cfg.namespace == "default"
        assert cfg.api_key is None
        assert cfg.tls is False

    def test_base_url_http(self) -> None:
        cfg = TemporalConfig(host="temporal.example.com", port=7233, tls=False)
        assert cfg.base_url == "http://temporal.example.com:7233"

    def test_base_url_https(self) -> None:
        cfg = TemporalConfig(host="temporal.example.com", port=7233, tls=True)
        assert cfg.base_url == "https://temporal.example.com:7233"

    def test_connection_verified(self, config: TemporalConfig) -> None:
        assert config.connection_verified is True

    def test_connection_verified_empty_host(self) -> None:
        cfg = TemporalConfig(host="", port=7233)
        assert cfg.connection_verified is False

    def test_api_key_optional(self) -> None:
        cfg = TemporalConfig(api_key="my-secret-key")
        assert cfg.api_key == "my-secret-key"

    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEMPORAL_HOST", "temporal-prod.example.com")
        monkeypatch.setenv("TEMPORAL_PORT", "7234")
        monkeypatch.setenv("TEMPORAL_NAMESPACE", "production")
        monkeypatch.setenv("TEMPORAL_API_KEY", "abc123")
        monkeypatch.setenv("TEMPORAL_TLS", "true")

        cfg = load_temporal_config_from_env()
        assert cfg.host == "temporal-prod.example.com"
        assert cfg.port == 7234
        assert cfg.namespace == "production"
        assert cfg.api_key == "abc123"
        assert cfg.tls is True


# TemporalClient

class TestTemporalClient:
    def test_auth_header_with_api_key(self) -> None:
        cfg = TemporalConfig(api_key="tok-123")
        c = TemporalClient(cfg)
        assert c._headers["Authorization"] == "Bearer tok-123"

    def test_no_auth_header_without_api_key(self, config: TemporalConfig) -> None:
        c = TemporalClient(config)
        assert "Authorization" not in c._headers

    @patch("app.services.temporal.client.httpx.Client")
    def test_list_workflows_success(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "executions": [
                {
                    "execution": {"workflowId": "wf-1", "runId": "run-1"},
                    "type": {"name": "OrderWorkflow"},
                    "status": "FAILED",
                    "startTime": "2024-01-15T10:00:00Z",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_response

        executions = client.list_workflows(query="ExecutionStatus='Failed'")
        assert len(executions) == 1
        assert executions[0]["execution"]["workflowId"] == "wf-1"
        assert executions[0]["status"] == "FAILED"

    @patch("app.services.temporal.client.httpx.Client")
    def test_list_workflows_empty(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"executions": []}
        mock_response.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__.return_value.post.return_value = mock_response

        executions = client.list_workflows()
        assert executions == []

    @patch("app.services.temporal.client.httpx.Client")
    def test_get_workflow_history_success(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "history": {
                "events": [
                    {
                        "eventId": "1",
                        "eventTime": "2024-01-15T10:00:00Z",
                        "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                    },
                    {
                        "eventId": "2",
                        "eventTime": "2024-01-15T10:01:00Z",
                        "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                        "workflowExecutionFailedEventAttributes": {
                            "failure": {
                                "message": "activity timeout",
                                "stackTrace": "at OrderActivity.java:42",
                            }
                        },
                    },
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__.return_value.get.return_value = mock_response

        events = client.get_workflow_history("wf-1", "run-1")
        assert len(events) == 2
        assert events[1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED"

    @patch("app.services.temporal.client.httpx.Client")
    def test_list_task_queues_success(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "pollers": [
                {
                    "identity": "worker-1@host",
                    "lastAccessTime": "2024-01-15T10:00:00Z",
                    "ratePerSecond": 100.0,
                }
            ],
            "taskQueueStatus": {"backlogCountHint": 0, "readLevel": 42},
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__.return_value.get.return_value = mock_response

        data = client.list_task_queues("payment-task-queue")
        assert len(data["pollers"]) == 1
        assert data["pollers"][0]["identity"] == "worker-1@host"

    @patch("app.services.temporal.client.httpx.Client")
    def test_get_namespace_metrics_success(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "namespaceInfo": {
                "name": "default",
                "state": "Registered",
                "description": "Default namespace",
            },
            "config": {"workflowExecutionRetentionTtl": "72h"},
            "replicationConfig": {"activeClusterName": "us-east"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx.return_value.__enter__.return_value.get.return_value = mock_response

        data = client.get_namespace_metrics()
        assert data["namespaceInfo"]["name"] == "default"

    @patch("app.services.temporal.client.httpx.Client")
    def test_client_error_on_http_failure(
        self, mock_httpx: MagicMock, client: TemporalClient
    ) -> None:
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_httpx.return_value.__enter__.return_value.post.side_effect = (
            httpx.HTTPStatusError("503", request=MagicMock(), response=mock_response)
        )

        with pytest.raises(TemporalClientError, match="503"):
            client.list_workflows()


# Tools

class TestTemporalListWorkflowsTool:
    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_returns_executions(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [
            {
                "execution": {"workflowId": "wf-1", "runId": "run-1"},
                "type": {"name": "OrderWorkflow"},
                "status": "FAILED",
                "startTime": "2024-01-15T10:00:00Z",
            }
        ]
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        result = tool._run(query="ExecutionStatus='Failed'", page_size=10)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["workflow_id"] == "wf-1"
        assert parsed[0]["status"] == "FAILED"

    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_empty_returns_message(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        result = tool._run()
        assert "No workflow" in result

    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_error_handled_gracefully(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_workflows.side_effect = TemporalClientError("connection refused")
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        result = tool._run()
        assert "Error" in result
        assert "connection refused" in result


class TestTemporalWorkflowHistoryTool:
    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_returns_events_with_failure(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.get_workflow_history.return_value = [
            {
                "eventId": "1",
                "eventTime": "2024-01-15T10:00:00Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "workflowExecutionFailedEventAttributes": {
                    "failure": {
                        "message": "activity timed out",
                        "cause": {"message": "deadline exceeded"},
                        "stackTrace": "at Workflow.java:99",
                    }
                },
            }
        ]
        mock_client_cls.return_value = mock_client

        tool = TemporalWorkflowHistoryTool()
        result = tool._run(workflow_id="wf-1", run_id="run-1")
        parsed = json.loads(result)
        assert parsed[0]["failure"]["message"] == "activity timed out"
        assert parsed[0]["failure"]["cause"] == "deadline exceeded"

    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_no_history_returns_message(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.get_workflow_history.return_value = []
        mock_client_cls.return_value = mock_client

        tool = TemporalWorkflowHistoryTool()
        result = tool._run(workflow_id="wf-1", run_id="run-1")
        assert "No history" in result


class TestTemporalTaskQueueTool:
    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_returns_pollers(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_task_queues.return_value = {
            "pollers": [
                {
                    "identity": "worker-1@host",
                    "lastAccessTime": "2024-01-15T10:00:00Z",
                    "ratePerSecond": 100.0,
                }
            ],
            "taskQueueStatus": {"backlogCountHint": 5, "readLevel": 10},
        }
        mock_client_cls.return_value = mock_client

        tool = TemporalTaskQueueTool()
        result = tool._run(task_queue="payment-task-queue")
        parsed = json.loads(result)
        assert parsed["poller_count"] == 1
        assert parsed["pollers"][0]["identity"] == "worker-1@host"
        assert parsed["backlog_count"] == 5

    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_warns_when_no_pollers(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_task_queues.return_value = {
            "pollers": [],
            "taskQueueStatus": {},
        }
        mock_client_cls.return_value = mock_client

        tool = TemporalTaskQueueTool()
        result = tool._run(task_queue="orphan-queue")
        parsed = json.loads(result)
        assert "warning" in parsed
        assert "No workers" in parsed["warning"]


class TestTemporalNamespaceMetricsTool:
    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_returns_namespace_info(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.get_namespace_metrics.return_value = {
            "namespaceInfo": {
                "name": "production",
                "state": "Registered",
                "description": "Production namespace",
            },
            "config": {"workflowExecutionRetentionTtl": "72h"},
            "replicationConfig": {
                "activeClusterName": "us-east",
                "clusters": [{"clusterName": "us-east"}],
            },
        }
        mock_client.get_workflow_count.return_value = {"count": 42}
        mock_client_cls.return_value = mock_client

        tool = TemporalNamespaceMetricsTool()
        result = tool._run()
        parsed = json.loads(result)
        assert parsed["namespace"] == "production"
        assert parsed["active_cluster"] == "us-east"
        assert parsed["retention_days"] == "72h"
        assert parsed["open_workflow_count"] == 42

    @patch("app.tools.TemporalTool.tool.TemporalClient")
    def test_namespace_info_returned_even_if_count_fails(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.get_namespace_metrics.return_value = {
            "namespaceInfo": {
                "name": "production",
                "state": "Registered",
                "description": "Production namespace",
            },
            "config": {"workflowExecutionRetentionTtl": "72h"},
            "replicationConfig": {
                "activeClusterName": "us-east",
                "clusters": [{"clusterName": "us-east"}],
            },
        }
        mock_client.get_workflow_count.side_effect = TemporalClientError("endpoint not found")
        mock_client_cls.return_value = mock_client

        tool = TemporalNamespaceMetricsTool()
        result = tool._run()
        parsed = json.loads(result)
        assert parsed["namespace"] == "production"
        assert parsed["active_cluster"] == "us-east"
        assert parsed["retention_days"] == "72h"
        assert parsed["open_workflow_count"] is None

class TestGetTemporalTools:
    def test_returns_four_tools(self) -> None:
        tools = get_temporal_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert names == {
            "temporal_list_workflows",
            "temporal_workflow_history",
            "temporal_task_queue",
            "temporal_namespace_metrics",
        }

    def test_accepts_custom_config(self) -> None:
        cfg = TemporalConfig(host="prod-temporal", namespace="prod")
        tools = get_temporal_tools(config=cfg)
        assert all(t.config == cfg for t in tools)
