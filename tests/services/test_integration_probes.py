from __future__ import annotations

import pytest

from app.integrations.config_models import CoralogixIntegrationConfig
from app.services.alertmanager.client import AlertmanagerClient, AlertmanagerConfig
from app.services.coralogix.client import CoralogixClient
from app.services.datadog.client import DatadogClient, DatadogConfig


def test_datadog_probe_access_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DatadogClient,
        "list_monitors",
        lambda _self: {"success": True, "monitors": [{"id": 1}], "total": 1},
    )
    client = DatadogClient(DatadogConfig(api_key="dd-api", app_key="dd-app", site="datadoghq.com"))

    result = client.probe_access()

    assert result.status == "passed"
    assert "1 monitors" in result.detail


def test_coralogix_probe_access_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CoralogixClient,
        "validate_access",
        lambda _self: {"success": True, "total": 1, "warnings": []},
    )
    client = CoralogixClient(
        CoralogixIntegrationConfig(
            api_key="cx-key",
            base_url="https://api.coralogix.com",
            application_name="payments",
            subsystem_name="worker",
        )
    )

    result = client.probe_access()

    assert result.status == "passed"
    assert "payments" in result.detail
    assert "worker" in result.detail


def test_alertmanager_probe_access_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        AlertmanagerClient,
        "get_status",
        lambda _self: {"success": True, "status": {"cluster": {"status": "ready"}}},
    )
    client = AlertmanagerClient(AlertmanagerConfig(base_url="https://alerts.example.com"))

    result = client.probe_access()

    assert result.status == "passed"
    assert "ready" in result.detail
