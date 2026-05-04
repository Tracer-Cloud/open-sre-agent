"""VictoriaLogs E2E tests verifying integration with the investigation pipeline.

Covers:
- VictoriaLogs config resolution from store and env
- Verifier status contract (passed / missing / failed)
- Source detection populates sources["victoria_logs"]
- Tool importability + executor-path contract
- Alert fixture validity (Alertmanager webhook format)

All HTTP traffic is mocked; no live broker is required. The live-broker proof
lives in the asciinema demo recording attached to PR #1144.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations


class TestVictoriaLogsIntegrationResolution:
    """VictoriaLogs config resolution from the local store."""

    def test_resolution_from_store(self) -> None:
        integrations = [
            {
                "id": "victoria-logs-prod",
                "service": "victoria_logs",
                "status": "active",
                "credentials": {
                    "base_url": "http://vmlogs.monitoring.svc:9428",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" in resolved
        assert resolved["victoria_logs"]["base_url"] == "http://vmlogs.monitoring.svc:9428"
        assert resolved["victoria_logs"]["tenant_id"] is None

    def test_resolution_with_tenant_id(self) -> None:
        integrations = [
            {
                "id": "victoria-logs-multi",
                "service": "victoria_logs",
                "status": "active",
                "credentials": {
                    "base_url": "https://vmlogs.example.com",
                    "tenant_id": "team-payments",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" in resolved
        assert resolved["victoria_logs"]["tenant_id"] == "team-payments"

    def test_alias_victorialogs_resolves_to_canonical_key(self) -> None:
        """The ``victorialogs`` alias normalizes to ``victoria_logs``."""
        integrations = [
            {
                "id": "vl-alias",
                "service": "victorialogs",
                "status": "active",
                "credentials": {
                    "base_url": "http://vmlogs:9428",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" in resolved
        assert resolved["victoria_logs"]["base_url"] == "http://vmlogs:9428"

    def test_empty_base_url_skipped(self) -> None:
        integrations = [
            {
                "id": "bad-victoria-logs",
                "service": "victoria_logs",
                "status": "active",
                "credentials": {"base_url": ""},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" not in resolved

    def test_inactive_integration_skipped(self) -> None:
        integrations = [
            {
                "id": "victoria-logs-inactive",
                "service": "victoria_logs",
                "status": "inactive",
                "credentials": {"base_url": "http://vmlogs:9428"},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" not in resolved

    def test_url_trailing_slash_normalized(self) -> None:
        integrations = [
            {
                "id": "victoria-logs-slash",
                "service": "victoria_logs",
                "status": "active",
                "credentials": {"base_url": "http://vmlogs.example.com:9428/"},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "victoria_logs" in resolved
        assert resolved["victoria_logs"]["base_url"] == "http://vmlogs.example.com:9428"


class TestVictoriaLogsEnvResolution:
    """VictoriaLogs config resolution from environment variables."""

    def test_resolved_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("VICTORIA_LOGS_URL", "http://vmlogs.monitoring.svc:9428")
        monkeypatch.delenv("VICTORIA_LOGS_TENANT_ID", raising=False)

        env_integrations = _load_env_integrations()
        records = [i for i in env_integrations if i["service"] == "victoria_logs"]

        assert len(records) == 1
        creds = records[0]["credentials"]
        assert creds["base_url"] == "http://vmlogs.monitoring.svc:9428"
        assert creds["tenant_id"] is None

    def test_tenant_id_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("VICTORIA_LOGS_URL", "https://vmlogs.example.com")
        monkeypatch.setenv("VICTORIA_LOGS_TENANT_ID", "acme")

        env_integrations = _load_env_integrations()
        records = [i for i in env_integrations if i["service"] == "victoria_logs"]

        assert len(records) == 1
        assert records[0]["credentials"]["tenant_id"] == "acme"

    def test_not_loaded_when_url_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("VICTORIA_LOGS_URL", raising=False)

        env_integrations = _load_env_integrations()
        records = [i for i in env_integrations if i["service"] == "victoria_logs"]

        assert len(records) == 0


class TestVictoriaLogsSourceDetection:
    """VictoriaLogs source detection in investigation context."""

    def test_detected_when_configured(self) -> None:
        raw_alert = {
            "commonLabels": {"alertname": "HighErrorRate", "service": "checkout-api"},
            "commonAnnotations": {"summary": "Error rate spike"},
        }
        resolved = {
            "victoria_logs": {
                "base_url": "http://vmlogs.monitoring.svc:9428",
                "tenant_id": None,
            }
        }

        sources = detect_sources(raw_alert, {}, resolved)

        assert "victoria_logs" in sources
        assert sources["victoria_logs"]["base_url"] == "http://vmlogs.monitoring.svc:9428"
        assert sources["victoria_logs"]["connection_verified"] is True

    def test_tenant_id_propagates_to_source(self) -> None:
        resolved = {
            "victoria_logs": {
                "base_url": "https://vmlogs.example.com",
                "tenant_id": "team-payments",
            }
        }

        sources = detect_sources({}, {}, resolved)

        assert sources["victoria_logs"]["tenant_id"] == "team-payments"

    def test_not_detected_when_unconfigured(self) -> None:
        raw_alert = {"commonLabels": {"alertname": "HighErrorRate"}}

        sources = detect_sources(raw_alert, {}, {})

        assert "victoria_logs" not in sources

    def test_not_detected_when_base_url_empty(self) -> None:
        resolved = {"victoria_logs": {"base_url": ""}}

        sources = detect_sources({}, {}, resolved)

        assert "victoria_logs" not in sources


class TestVictoriaLogsVerification:
    """VictoriaLogs verifier status contract — must return passed/missing/failed."""

    def test_verify_success(self, monkeypatch) -> None:
        """Successful query probe returns status=passed."""
        monkeypatch.setenv("VICTORIA_LOGS_URL", "http://vmlogs.monitoring.svc:9428")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"_msg":"sample"}\n'
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client.get", return_value=mock_response):
            from app.integrations.verify import verify_integrations

            results = verify_integrations(service="victoria_logs")

        result = next((r for r in results if r["service"] == "victoria_logs"), None)
        assert result is not None
        assert result["status"] == "passed", (
            "Verifier must return status='passed' on success — verification_exit_code() "
            "checks for 'passed' specifically; any other string silently fails the core gate."
        )
        assert "vmlogs.monitoring.svc:9428" in result["detail"]

    def test_verify_failure_on_http_error(self, monkeypatch) -> None:
        """HTTP error from VictoriaLogs returns status=failed."""
        monkeypatch.setenv("VICTORIA_LOGS_URL", "http://vmlogs.monitoring.svc:9428")

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "service unavailable"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("httpx.Client.get", return_value=mock_response):
            from app.integrations.verify import verify_integrations

            results = verify_integrations(service="victoria_logs")

        result = next((r for r in results if r["service"] == "victoria_logs"), None)
        assert result is not None
        assert result["status"] == "failed"
        assert "503" in result["detail"]

    def test_verify_missing_when_not_configured(self, monkeypatch) -> None:
        """No URL configured returns status=missing (not failed)."""
        monkeypatch.delenv("VICTORIA_LOGS_URL", raising=False)

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="victoria_logs")

        result = next((r for r in results if r["service"] == "victoria_logs"), None)
        assert result is not None
        assert result["status"] == "missing"

    def test_verify_result_structure(self, monkeypatch) -> None:
        """Verify result has the canonical {service, source, status, detail} shape."""
        monkeypatch.delenv("VICTORIA_LOGS_URL", raising=False)

        from app.integrations.verify import verify_integrations

        results = verify_integrations(service="victoria_logs")
        for result in results:
            if result["service"] == "victoria_logs":
                assert set(result.keys()) >= {"service", "source", "status", "detail"}
                assert result["status"] in ("passed", "missing", "failed")


class TestVictoriaLogsToolAvailability:
    """VictoriaLogsTool importability + executor-path contract."""

    def test_tool_importable(self) -> None:
        from app.tools.VictoriaLogsTool import VictoriaLogsTool, victoria_logs_query

        assert victoria_logs_query is not None
        assert isinstance(victoria_logs_query, VictoriaLogsTool)
        assert victoria_logs_query.name == "victoria_logs_query"
        assert victoria_logs_query.source == "victoria_logs"

    def test_tool_unavailable_without_source(self) -> None:
        from app.tools.VictoriaLogsTool import victoria_logs_query

        assert not victoria_logs_query.is_available({})
        assert not victoria_logs_query.is_available({"victoria_logs": {}})
        assert not victoria_logs_query.is_available({"victoria_logs": {"base_url": ""}})

    def test_tool_available_with_configured_source(self) -> None:
        from app.tools.VictoriaLogsTool import victoria_logs_query

        sources = {"victoria_logs": {"base_url": "http://vmlogs:9428"}}
        assert victoria_logs_query.is_available(sources)

    def test_tool_extract_params_returns_all_run_kwargs(self) -> None:
        """Executor invokes ``run(**extract_params(sources))`` — extract_params must
        surface every kwarg run() declares; otherwise the tool is permanently inert
        from the executor path. This is the regression that broke prior PRs #663/#1060.
        """
        from app.tools.VictoriaLogsTool import victoria_logs_query

        sources = {
            "victoria_logs": {
                "base_url": "http://vmlogs.monitoring.svc:9428",
                "tenant_id": "team-payments",
            }
        }
        params = victoria_logs_query.extract_params(sources)

        # Every param run() declares MUST be present.
        assert params["base_url"] == "http://vmlogs.monitoring.svc:9428"
        assert params["tenant_id"] == "team-payments"
        assert "query" in params
        assert "limit" in params
        assert "start" in params

    @patch("app.tools.VictoriaLogsTool.make_victoria_logs_client")
    def test_tool_run_via_executor_path(self, mock_factory) -> None:
        """Full executor flow: extract_params → run(**params) → success."""
        from app.tools.VictoriaLogsTool import victoria_logs_query

        mock_client = mock_factory.return_value
        mock_client.__enter__.return_value = mock_client
        mock_client.query_logs.return_value = {
            "success": True,
            "rows": [
                {
                    "_msg": "POST /api/checkout 503 Service Unavailable",
                    "level": "error",
                    "service": "checkout-api",
                    "error": "psycopg2.pool.PoolError: connection pool exhausted",
                }
            ],
            "total": 1,
        }

        sources = {
            "victoria_logs": {
                "base_url": "http://vmlogs.monitoring.svc:9428",
                "tenant_id": None,
            }
        }
        params = victoria_logs_query.extract_params(sources)
        result = victoria_logs_query.run(**params)

        assert result["available"] is True
        assert result["source"] == "victoria_logs"
        assert result["total"] == 1
        assert "psycopg2.pool.PoolError" in result["rows"][0]["error"]
        mock_factory.assert_called_once_with("http://vmlogs.monitoring.svc:9428", tenant_id=None)

    def test_tool_run_missing_base_url(self) -> None:
        from app.tools.VictoriaLogsTool import victoria_logs_query

        result = victoria_logs_query.run(base_url="")

        assert result["available"] is False
        assert "not configured" in result["error"]

    def test_tool_surfaces_includes_chat(self) -> None:
        """Tool must declare both surfaces — log-query tools should be visible to chat
        like SplunkSearchTool, not investigation-only (the registry default for
        class-based tools without explicit ``surfaces``).
        """
        from app.tools.VictoriaLogsTool import victoria_logs_query

        assert "investigation" in victoria_logs_query.surfaces
        assert "chat" in victoria_logs_query.surfaces


class TestVictoriaLogsAlertFixture:
    """Validate the alert fixture parses and conforms to the Alertmanager webhook format."""

    def test_fixture_is_valid_json(self) -> None:
        fixture_path = Path(__file__).parent / "victoria_logs_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)

    def test_fixture_has_required_webhook_fields(self) -> None:
        fixture_path = Path(__file__).parent / "victoria_logs_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        for key in ("version", "status", "commonLabels", "commonAnnotations", "alerts"):
            assert key in alert
        assert isinstance(alert["alerts"], list)
        assert len(alert["alerts"]) > 0

    def test_fixture_individual_alerts_have_prometheus_fields(self) -> None:
        fixture_path = Path(__file__).parent / "victoria_logs_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        for individual in alert["alerts"]:
            assert "fingerprint" in individual
            assert "generatorURL" in individual
            assert "startsAt" in individual

    def test_fixture_describes_checkout_api_scenario(self) -> None:
        """Fixture aligns with the seeded VictoriaLogs scenario for the demo."""
        fixture_path = Path(__file__).parent / "victoria_logs_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        common = alert["commonLabels"]
        assert common["service"] == "checkout-api"
        assert common["alertname"] == "HighErrorRate"
        assert common["severity"] == "critical"


class TestVictoriaLogsIntegrationConfig:
    """``VictoriaLogsIntegrationConfig`` model validation."""

    def test_config_creation(self) -> None:
        from app.integrations.models import VictoriaLogsIntegrationConfig

        config = VictoriaLogsIntegrationConfig(
            base_url="http://vmlogs:9428",
            tenant_id="acme",
            integration_id="vl-1",
        )
        assert config.base_url == "http://vmlogs:9428"
        assert config.tenant_id == "acme"
        assert config.integration_id == "vl-1"

    def test_config_url_normalization(self) -> None:
        from app.integrations.models import VictoriaLogsIntegrationConfig

        config = VictoriaLogsIntegrationConfig(base_url="  http://vmlogs.example.com:9428/  ")
        assert config.base_url == "http://vmlogs.example.com:9428"

    def test_config_empty_tenant_normalizes_to_none(self) -> None:
        """Empty/whitespace tenant_id collapses to None so AccountID header is omitted."""
        from app.integrations.models import VictoriaLogsIntegrationConfig

        for empty in ("", "   ", None):
            config = VictoriaLogsIntegrationConfig(
                base_url="http://vmlogs:9428",
                tenant_id=empty,
            )
            assert config.tenant_id is None
