"""The client cache must key on credentials and TLS config, not just account+endpoint.

``get_grafana_client_from_credentials`` cached the client under
``creds_{account_id}_{endpoint}``, so after a token rotation, a changed
Basic-Auth pair, or a changed ``verify_ssl``/``ca_bundle`` the same
(account_id, endpoint) returned the stale client carrying the old credentials.
The cache key now folds in a hashed credential/TLS fingerprint, so any auth/TLS
change constructs a fresh client while an identical config still reuses one.
"""

from __future__ import annotations

from typing import Any

import pytest

from integrations.grafana import client as grafana_client

_ACCOUNT_ID = "acct-1"
_ENDPOINT = "https://grafana.example.net"
_OLD_TOKEN = "old-token"
_NEW_TOKEN = "new-token"


@pytest.fixture(autouse=True)
def _clear_client_cache() -> Any:
    grafana_client._grafana_client_cache.clear()
    yield
    grafana_client._grafana_client_cache.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grafana_client.GrafanaClient, "discover_datasource_uids", lambda _self: {})


def test_rotated_api_key_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is not second
    assert second.read_token == _NEW_TOKEN
    assert first.read_token == _OLD_TOKEN


def test_rotated_credential_evicts_the_stale_entry() -> None:
    """The old token's cache entry must not linger forever across rotations."""
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert len(grafana_client._grafana_client_cache) == 1


def test_whitespace_only_difference_shares_cache_entry() -> None:
    """The fingerprint strips before hashing to match what the built client
    ends up with: ``GrafanaAccountConfig`` inherits a wildcard
    ``StrictConfigModel`` validator that strips every string field, so an
    api_key differing only by surrounding whitespace produces the identical
    ``read_token`` on the built client either way -- fingerprinting the raw,
    unstripped value would treat these as different credentials and build a
    redundant client for no real difference.
    """
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=f" {_OLD_TOKEN} ", account_id=_ACCOUNT_ID
    )

    assert first is second
    assert first.read_token == _OLD_TOKEN


def test_identical_config_reuses_cached_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is second


def test_equivalent_endpoint_shares_cache_entry() -> None:
    """A trailing slash is normalized, so it still hits the same cache entry."""
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT + "/", api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )

    assert first is second


def test_changed_verify_ssl_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=True
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=False
    )

    assert first is not second


def test_changed_ca_bundle_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, ca_bundle="/etc/ca-a.pem"
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, ca_bundle="/etc/ca-b.pem"
    )

    assert first is not second


def test_changed_basic_auth_builds_new_client() -> None:
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key="",
        account_id=_ACCOUNT_ID,
        username="alice",
        password="secret-a",
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key="",
        account_id=_ACCOUNT_ID,
        username="alice",
        password="secret-b",
    )

    assert first is not second


def test_different_account_id_does_not_evict_the_other_accounts_client() -> None:
    """Eviction is scoped to one (account, endpoint) identity, not global."""
    other = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id="acct-other"
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    assert len(grafana_client._grafana_client_cache) == 2
    unchanged = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id="acct-other"
    )
    assert unchanged is other
