from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.grafana.client import get_grafana_client


def test_get_grafana_client_reads_verify_ssl_and_ca_bundle_from_env(monkeypatch) -> None:
    """get_grafana_client() must forward GRAFANA_VERIFY_SSL/GRAFANA_CA_BUNDLE, not just
    the endpoint/token — otherwise env-only setups can never configure TLS trust."""
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "glsa_test")
    monkeypatch.setenv("GRAFANA_VERIFY_SSL", "false")
    monkeypatch.setenv("GRAFANA_CA_BUNDLE", "/etc/ssl/internal-ca.pem")

    with patch("integrations.grafana.client.get_grafana_client_from_credentials") as mock_factory:
        get_grafana_client()

    mock_factory.assert_called_once_with(
        endpoint="https://grafana.example.com",
        api_key="glsa_test",
        account_id="env_default",
        verify_ssl=False,
        ca_bundle="/etc/ssl/internal-ca.pem",
    )


def test_get_grafana_client_defaults_verify_ssl_true_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "glsa_test")
    monkeypatch.delenv("GRAFANA_VERIFY_SSL", raising=False)
    monkeypatch.delenv("GRAFANA_CA_BUNDLE", raising=False)

    with patch("integrations.grafana.client.get_grafana_client_from_credentials") as mock_factory:
        get_grafana_client()

    mock_factory.assert_called_once_with(
        endpoint="https://grafana.example.com",
        api_key="glsa_test",
        account_id="env_default",
        verify_ssl=True,
        ca_bundle="",
    )


def test_client_falls_back_to_env_datasource_uids_when_discovery_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env UID overrides must apply when live discovery returns nothing."""
    from integrations.grafana import client as grafana_client

    grafana_client._grafana_client_cache.clear()
    monkeypatch.setenv("GRAFANA_LOKI_DATASOURCE_UID", "env-loki")
    monkeypatch.setenv("GRAFANA_TEMPO_DATASOURCE_UID", "env-tempo")
    monkeypatch.setenv("GRAFANA_MIMIR_DATASOURCE_UID", "env-mimir")
    monkeypatch.setattr(grafana_client.GrafanaClient, "discover_datasource_uids", lambda _self: {})

    client = grafana_client.get_grafana_client_from_credentials(
        endpoint="http://grafana.example.com", api_key="token", account_id="fallback_test"
    )

    assert client.loki_datasource_uid == "env-loki"
    assert client.tempo_datasource_uid == "env-tempo"
    assert client.mimir_datasource_uid == "env-mimir"
    grafana_client._grafana_client_cache.clear()


def test_client_prefers_discovered_uids_over_env_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.grafana import client as grafana_client

    grafana_client._grafana_client_cache.clear()
    monkeypatch.setenv("GRAFANA_LOKI_DATASOURCE_UID", "env-loki")
    monkeypatch.setattr(
        grafana_client.GrafanaClient,
        "discover_datasource_uids",
        lambda _self: {"loki_uid": "discovered-loki", "tempo_uid": "discovered-tempo"},
    )

    client = grafana_client.get_grafana_client_from_credentials(
        endpoint="http://grafana.example.com", api_key="token", account_id="prefer_discovered"
    )

    assert client.loki_datasource_uid == "discovered-loki"
    assert client.tempo_datasource_uid == "discovered-tempo"
    # Mimir missing from discovery → env/cloud default fallback
    assert client.mimir_datasource_uid
    grafana_client._grafana_client_cache.clear()
