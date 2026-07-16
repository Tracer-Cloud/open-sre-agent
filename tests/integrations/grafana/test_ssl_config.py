"""Grafana TLS configuration tests."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from integrations.config_models import GrafanaIntegrationConfig
from integrations.grafana.client import (
    GrafanaClient,
    _grafana_client_cache,
    get_grafana_client,
    get_grafana_client_from_credentials,
)
from integrations.grafana.config import GrafanaAccountConfig


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    _grafana_client_cache.clear()


def test_grafana_integration_config_ssl_verify_prefers_ca_bundle() -> None:
    cfg = GrafanaIntegrationConfig(
        endpoint="https://grafana.example.com",
        api_key="token",
        verify_ssl=False,
        ca_bundle="/etc/ssl/corp-ca.pem",
    )
    assert cfg.ssl_verify == "/etc/ssl/corp-ca.pem"


def test_grafana_integration_config_verify_ssl_false() -> None:
    cfg = GrafanaIntegrationConfig(
        endpoint="https://grafana.example.com",
        api_key="token",
        verify_ssl="false",
    )
    assert cfg.verify_ssl is False
    assert cfg.ssl_verify is False


def test_grafana_account_config_ssl_verify() -> None:
    cfg = GrafanaAccountConfig(
        account_id="test",
        instance_url="https://grafana.example.com",
        read_token="token",
        verify_ssl=True,
    )
    assert cfg.ssl_verify is True


def test_grafana_client_uses_custom_ca_bundle_for_discovery() -> None:
    response = Mock()
    response.json.return_value = []
    config = GrafanaAccountConfig(
        account_id="test",
        instance_url="https://grafana.example.com",
        read_token="token",
        ca_bundle="/etc/ssl/corp-ca.pem",
    )

    with patch("integrations.grafana.base.requests.get", return_value=response) as request:
        GrafanaClient(config=config).discover_datasource_uids()

    request.assert_called_once_with(
        "https://grafana.example.com/api/datasources",
        headers={"Authorization": "Bearer token"},
        timeout=10,
        verify="/etc/ssl/corp-ca.pem",
    )


def test_grafana_client_cache_separates_tls_policies() -> None:
    with patch.object(GrafanaClient, "discover_datasource_uids", return_value={}):
        insecure = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token",
            verify_ssl="false",
        )
        secure = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token",
            verify_ssl=True,
        )

    assert insecure is not secure
    assert insecure.ssl_verify is False
    assert secure.ssl_verify is True


def test_get_grafana_client_reads_tls_environment(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "token")
    monkeypatch.setenv("GRAFANA_VERIFY_SSL", "false")
    monkeypatch.setenv("GRAFANA_CA_BUNDLE", "/etc/ssl/corp-ca.pem")

    with patch("integrations.grafana.client.get_grafana_client_from_credentials") as factory:
        get_grafana_client()

    factory.assert_called_once_with(
        endpoint="https://grafana.example.com",
        api_key="token",
        account_id="env_default",
        verify_ssl="false",
        ca_bundle="/etc/ssl/corp-ca.pem",
    )
