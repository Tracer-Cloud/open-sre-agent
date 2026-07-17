from __future__ import annotations

from unittest.mock import patch

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
