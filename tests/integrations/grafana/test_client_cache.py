"""The client cache must key on credentials and TLS config, not just account+endpoint.

``get_grafana_client_from_credentials`` cached the client under
``creds_{account_id}_{endpoint}``, so after a token rotation, a changed
Basic-Auth pair, or a changed ``verify_ssl``/``ca_bundle`` the SAME
(account_id, endpoint) returned the STALE client carrying the OLD credentials.
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
    # No network: discovery is the only round trip in the build path.
    yield
    grafana_client._grafana_client_cache.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        grafana_client.GrafanaClient,
        "discover_datasource_uids",
        lambda _self: {},
    )


def test_rotated_api_key_builds_new_client() -> None:
    """A different api_key for the same account+endpoint yields a fresh client with the new token."""
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )

    same_object_after_credential_change = first is second
    second_call_uses_new_token = second.read_token == _NEW_TOKEN

    assert same_object_after_credential_change is False
    assert second_call_uses_new_token is True
    assert first.read_token == _OLD_TOKEN


def test_identical_config_reuses_cached_client() -> None:
    """Two identical calls return the same cached object — caching still works."""
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
    """Flipping verify_ssl for the same account+endpoint+token yields a fresh client."""
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=True
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, verify_ssl=False
    )

    assert first is not second
    assert second._config.verify_ssl is False


def test_changed_ca_bundle_builds_new_client() -> None:
    """A different ca_bundle for the same account+endpoint+token yields a fresh client."""
    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID, ca_bundle=""
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key=_OLD_TOKEN,
        account_id=_ACCOUNT_ID,
        ca_bundle="/etc/ssl/custom.pem",
    )

    assert first is not second
    assert second._config.ca_bundle == "/etc/ssl/custom.pem"


def test_surrounding_whitespace_shares_cache_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inputs differing only by surrounding whitespace reuse one client (one discovery).

    ``GrafanaAccountConfig`` strips the endpoint and the credentials are used
    verbatim, so ``" old-token "`` and ``"old-token"`` are the same effective
    config. Fingerprinting the raw strings would build two clients and run
    discovery twice; normalizing before hashing collapses them to one.
    """
    discoveries = 0

    def _count_discovery(_self: Any) -> dict[str, str]:
        nonlocal discoveries
        discoveries += 1
        return {}

    monkeypatch.setattr(grafana_client.GrafanaClient, "discover_datasource_uids", _count_discovery)

    first = grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT,
        api_key=_OLD_TOKEN,
        account_id=_ACCOUNT_ID,
        ca_bundle="/etc/ssl/custom.pem",
    )
    second = grafana_client.get_grafana_client_from_credentials(
        endpoint="  " + _ENDPOINT + "  ",
        api_key="  " + _OLD_TOKEN + "  ",
        account_id=_ACCOUNT_ID,
        ca_bundle="  /etc/ssl/custom.pem  ",
    )

    assert first is second
    assert discoveries == 1


def test_rotation_evicts_superseded_entry() -> None:
    """Rotating the token twice leaves exactly one entry for (account, endpoint) — the newest.

    Without eviction each fingerprint would pin a permanent entry retaining an
    old secret, growing the cache on every rotation.
    """
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_OLD_TOKEN, account_id=_ACCOUNT_ID
    )
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=_NEW_TOKEN, account_id=_ACCOUNT_ID
    )
    newest_token = "newest-token"
    grafana_client.get_grafana_client_from_credentials(
        endpoint=_ENDPOINT, api_key=newest_token, account_id=_ACCOUNT_ID
    )

    # Count every cached client for this account+endpoint, independent of the
    # fingerprint helper, so the assertion pins eviction behavior directly.
    entries = [
        cached
        for key, cached in grafana_client._grafana_client_cache.items()
        if _ACCOUNT_ID in key and _ENDPOINT in key
    ]

    assert len(entries) == 1
    assert entries[0].read_token == newest_token
