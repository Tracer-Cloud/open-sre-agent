from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.grafana.client import (
    clear_grafana_client_cache,
    get_grafana_client,
    get_grafana_client_from_credentials,
)


@pytest.fixture(autouse=True)
def _clear_grafana_client_cache():
    clear_grafana_client_cache()
    yield
    clear_grafana_client_cache()


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


def _patched_discovery():
    return patch(
        "integrations.grafana.client.GrafanaClient.discover_datasource_uids",
        return_value={},
    )


def test_get_grafana_client_from_credentials_rebuilds_on_token_rotation() -> None:
    """#4192: rotating a token must not keep returning the client built from
    the previous one — the cache key ignored api_key entirely."""
    with _patched_discovery():
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            account_id="user_integration",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-two",
            account_id="user_integration",
        )

    assert first is not second
    assert second.read_token == "token-two"


def test_get_grafana_client_from_credentials_rebuilds_on_tls_change() -> None:
    """Basic auth and TLS settings must also invalidate the cached client."""
    with _patched_discovery():
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token",
            account_id="user_integration",
            verify_ssl=True,
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token",
            account_id="user_integration",
            verify_ssl=False,
            ca_bundle="/etc/ssl/internal-ca.pem",
        )

    assert first is not second
    assert second.ssl_verify != first.ssl_verify


def test_get_grafana_client_from_credentials_reuses_client_for_same_config() -> None:
    """Identical normalized configuration should still hit the cache."""
    with _patched_discovery():
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            account_id="user_integration",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            account_id="user_integration",
        )

    assert first is second


def test_get_grafana_client_from_credentials_evicts_stale_cache_entry() -> None:
    """Rotating a token should not leave the old client cached forever."""
    with _patched_discovery():
        get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            account_id="user_integration",
        )
        get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-two",
            account_id="user_integration",
        )

    from integrations.grafana.client import _grafana_client_cache

    matching = [
        client
        for client in _grafana_client_cache.values()
        if client.instance_url == "https://grafana.example.com"
        and client.account_id == "user_integration"
    ]
    assert len(matching) == 1
    assert matching[0].read_token == "token-two"
