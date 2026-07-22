from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from integrations.grafana.client import (
    GrafanaClient,
    clear_grafana_client_cache,
    get_grafana_client,
    get_grafana_client_from_credentials,
)


@pytest.fixture(autouse=True)
def _reset_grafana_client_cache() -> Iterator[None]:
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


def test_client_cache_reuses_identical_connection_config() -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ) as discover:
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
        )

    assert second is first
    discover.assert_called_once()


def test_client_cache_does_not_reuse_client_after_token_rotation() -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ):
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-two",
        )

    assert second is not first
    assert first.read_token == "token-one"
    assert second.read_token == "token-two"


def test_client_cache_isolates_basic_auth_credentials() -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ):
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="",
            username="first-user",
            password="first-password",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="",
            username="second-user",
            password="second-password",
        )

    assert second is not first
    assert first.username == "first-user"
    assert second.username == "second-user"


@pytest.mark.parametrize(
    ("first_verify_ssl", "first_ca_bundle", "second_verify_ssl", "second_ca_bundle"),
    [
        (True, "", False, ""),
        (True, "/etc/ssl/first.pem", True, "/etc/ssl/second.pem"),
    ],
)
def test_client_cache_isolates_tls_configuration(
    first_verify_ssl: bool,
    first_ca_bundle: str,
    second_verify_ssl: bool,
    second_ca_bundle: str,
) -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ):
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            verify_ssl=first_verify_ssl,
            ca_bundle=first_ca_bundle,
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
            verify_ssl=second_verify_ssl,
            ca_bundle=second_ca_bundle,
        )

    assert second is not first
    assert first._config.ssl_verify == (first_ca_bundle or first_verify_ssl)
    assert second._config.ssl_verify == (second_ca_bundle or second_verify_ssl)


def test_client_cache_normalizes_equivalent_endpoints() -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ) as discover:
        first = get_grafana_client_from_credentials(
            endpoint=" https://grafana.example.com/ ",
            api_key="token-one",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="token-one",
        )

    assert second is first
    assert second.instance_url == "https://grafana.example.com"
    discover.assert_called_once()


def test_client_cache_isolates_account_ids_at_same_endpoint() -> None:
    with patch.object(
        GrafanaClient,
        "discover_datasource_uids",
        return_value={"loki_uid": "loki"},
    ):
        first = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="shared-token",
            account_id="first-account",
        )
        second = get_grafana_client_from_credentials(
            endpoint="https://grafana.example.com",
            api_key="shared-token",
            account_id="second-account",
        )

    assert second is not first
    assert first.account_id == "first-account"
    assert second.account_id == "second-account"
