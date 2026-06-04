"""Unit tests for the Temporal integration.

All tests use mocks — no live Temporal server required.
Run with: pytest tests/integrations/test_temporal.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.integrations.temporal import TemporalConfig, load_temporal_config_from_env
from app.services.temporal.client import TemporalClient, TemporalClientError
from app.tools.registry import clear_tool_registry_cache, get_registered_tools
from app.tools.TemporalTool import (
    TemporalListWorkflowsTool,
    TemporalNamespaceMetricsTool,
    TemporalTaskQueueTool,
    TemporalWorkflowHistoryTool,
    get_temporal_tools,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config() -> TemporalConfig:
    return TemporalConfig(host="localhost", port=7233, namespace="default")


@pytest.fixture()
def client(config: TemporalConfig) -> TemporalClient:
    return TemporalClient(config)


# ---------------------------------------------------------------------------
# TemporalConfig
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TemporalClient
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class TestTemporalListWorkflowsTool:
    @patch("app.tools.TemporalTool.TemporalClient")
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
        result = tool.run(host="localhost", query="ExecutionStatus='Failed'", page_size=10)
        assert result["available"] is True
        assert result["total"] == 1
        assert result["total_failed"] == 1

    @patch("app.tools.TemporalTool.TemporalClient")
    def test_empty_returns_empty_list(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        result = tool.run(host="localhost")
        assert result["available"] is True
        assert result["total"] == 0

    @patch("app.tools.TemporalTool.TemporalClient")
    def test_error_handled_gracefully(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_workflows.side_effect = TemporalClientError("connection refused")
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        result = tool.run(host="localhost")
        assert result["available"] is False
        assert "connection refused" in result["error"]

    def test_missing_host_returns_error(self) -> None:
        tool = TemporalListWorkflowsTool()
        result = tool.run(host="")
        assert result["available"] is False
        assert "host is required" in result["error"]


class TestTemporalWorkflowHistoryTool:
    @patch("app.tools.TemporalTool.TemporalClient")
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
        result = tool.run(host="localhost", workflow_id="wf-1", run_id="run-1")
        assert result["available"] is True
        assert result["total_failure_events"] == 1

    @patch("app.tools.TemporalTool.TemporalClient")
    def test_no_history_returns_empty(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.get_workflow_history.return_value = []
        mock_client_cls.return_value = mock_client

        tool = TemporalWorkflowHistoryTool()
        result = tool.run(host="localhost", workflow_id="wf-1", run_id="run-1")
        assert result["available"] is True
        assert result["total_events"] == 0

    def test_missing_host_returns_error(self) -> None:
        tool = TemporalWorkflowHistoryTool()
        result = tool.run(host="", workflow_id="wf-1", run_id="run-1")
        assert result["available"] is False
        assert "host is required" in result["error"]


class TestTemporalTaskQueueTool:
    @patch("app.tools.TemporalTool.TemporalClient")
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
        result = tool.run(host="localhost", task_queue="payment-task-queue")
        assert result["available"] is True
        assert result["poller_count"] == 1
        assert result["pollers"][0]["identity"] == "worker-1@host"
        assert result["backlog_count"] == 5

    @patch("app.tools.TemporalTool.TemporalClient")
    def test_warns_when_no_pollers(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.list_task_queues.return_value = {
            "pollers": [],
            "taskQueueStatus": {},
        }
        mock_client_cls.return_value = mock_client

        tool = TemporalTaskQueueTool()
        result = tool.run(host="localhost", task_queue="orphan-queue")
        assert result["available"] is True
        assert result["poller_count"] == 0
        assert "warning" in result
        assert len(result["unhealthy_queues"]) == 1

    def test_missing_host_returns_error(self) -> None:
        tool = TemporalTaskQueueTool()
        result = tool.run(host="", task_queue="payment-task-queue")
        assert result["available"] is False
        assert "host is required" in result["error"]


class TestTemporalNamespaceMetricsTool:
    @patch("app.tools.TemporalTool.TemporalClient")
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
        result = tool.run(host="localhost", namespace="production")
        assert result["available"] is True
        assert result["namespace"] == "production"
        assert result["active_cluster"] == "us-east"
        assert result["retention_days"] == "72h"
        assert result["open_workflow_count"] == 42

    @patch("app.tools.TemporalTool.TemporalClient")
    def test_namespace_info_returned_even_if_count_fails(
        self, mock_client_cls: MagicMock
    ) -> None:
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
        result = tool.run(host="localhost", namespace="production")
        assert result["available"] is True
        assert result["namespace"] == "production"
        assert result["active_cluster"] == "us-east"
        assert result["retention_days"] == "72h"
        assert result["open_workflow_count"] is None

    def test_missing_host_returns_error(self) -> None:
        tool = TemporalNamespaceMetricsTool()
        result = tool.run(host="")
        assert result["available"] is False
        assert "host is required" in result["error"]


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

    def test_temporal_registered_in_integration_registry(self) -> None:
        from app.integrations.registry import INTEGRATION_SPECS_BY_SERVICE
        assert "temporal" in INTEGRATION_SPECS_BY_SERVICE

    def test_temporal_tools_have_valid_input_schemas(self) -> None:
        tools = get_temporal_tools()
        for t in tools:
            assert "properties" in t.input_schema
            assert "required" in t.input_schema
            assert "host" in t.input_schema["required"]

    def test_temporal_tools_wired_with_integration_config(self) -> None:
        from app.integrations.config_models import TemporalIntegrationConfig
        from app.integrations.temporal import load_temporal_config_from_integration

        integration_cfg = TemporalIntegrationConfig(
            host="temporal.prod.example.com",
            port=7233,
            namespace="production",
        )
        resolved = load_temporal_config_from_integration(integration_cfg)
        assert resolved.host == "temporal.prod.example.com"
        assert resolved.namespace == "production"

    def test_temporal_source_registered_in_evidence_types(self) -> None:
        from app.types.evidence import EvidenceSource
        assert "temporal" in EvidenceSource.__args__  # type: ignore[attr-defined]
    @patch("app.tools.TemporalTool.TemporalClient")
    def test_integration_config_values_reach_client(self, mock_client_cls: MagicMock) -> None:
        """Verify port, api_key, tls from integration config reach TemporalClient."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_cls.return_value = mock_client

        tool = TemporalListWorkflowsTool()
        tool.run(
            host="temporal.cloud.example.com",
            namespace="production",
            port=7234,
            api_key="my-secret-key",
            tls=True,
        )

        called_config = mock_client_cls.call_args[0][0]
        assert called_config.host == "temporal.cloud.example.com"
        assert called_config.port == 7234
        assert called_config.api_key == "my-secret-key"
        assert called_config.tls is True
        assert called_config.namespace == "production"

    def test_temporal_tools_discovered_by_registry(self) -> None:
        """Temporal tools must be auto-discovered by the investigation registry."""

        clear_tool_registry_cache()
        tools = get_registered_tools("investigation")
        tool_names = {t.name for t in tools}

        assert "temporal_list_workflows" in tool_names
        assert "temporal_workflow_history" in tool_names
        assert "temporal_task_queue" in tool_names
        assert "temporal_namespace_metrics" in tool_names

    def test_temporal_tools_in_get_available_actions(self) -> None:
        """Temporal tools must appear in get_available_actions() used by the agent."""
        from app.tools.investigation_registry.actions import get_available_actions
        from app.tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        actions = get_available_actions()
        action_names = {a.name for a in actions}

        assert "temporal_list_workflows" in action_names
