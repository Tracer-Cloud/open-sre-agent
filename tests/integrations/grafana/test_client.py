from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from integrations.grafana.client import get_grafana_client, get_grafana_client_from_credentials


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


# ---------------------------------------------------------------------------
# Stampede / single-flight tests (issue #4245)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_grafana_cache():
    """Isolate each test from module-level cache state."""
    import integrations.grafana.client as m

    original_cache = dict(m._grafana_client_cache)
    original_locks = dict(m._grafana_client_locks)
    m._grafana_client_cache.clear()
    m._grafana_client_locks.clear()
    yield
    m._grafana_client_cache.clear()
    m._grafana_client_cache.update(original_cache)
    m._grafana_client_locks.clear()
    m._grafana_client_locks.update(original_locks)


def _make_fake_grafana_client(discover_return: dict) -> MagicMock:
    """Return a GrafanaClient stand-in whose discover_datasource_uids is controlled."""
    client = MagicMock()
    client.discover_datasource_uids.return_value = discover_return
    return client


class TestClientCaching:
    def test_second_call_returns_cached_client(self) -> None:
        """A second call with the same credentials must not call discover again."""
        with patch("integrations.grafana.client.GrafanaClient") as MockClient:
            instance = _make_fake_grafana_client({})
            MockClient.return_value = instance

            first = get_grafana_client_from_credentials(
                endpoint="https://g.example.com",
                api_key="tok",
                account_id="acct1",
            )
            second = get_grafana_client_from_credentials(
                endpoint="https://g.example.com",
                api_key="tok",
                account_id="acct1",
            )

        assert first is second
        # discover_datasource_uids must have been called exactly once
        assert instance.discover_datasource_uids.call_count == 1

    def test_different_endpoints_get_separate_clients(self) -> None:
        """Different (account_id, endpoint) pairs are cached independently."""
        call_count: list[int] = [0]

        def _fake_discover() -> dict:
            call_count[0] += 1
            return {}

        with patch("integrations.grafana.client.GrafanaClient") as MockClient:
            MockClient.side_effect = lambda _config: _make_fake_grafana_client(
                {"called": call_count[0]}
            )
            # Patch discover on every instance by wrapping side_effect
            instances: list[MagicMock] = []

            def _mk(config):  # type: ignore[override]
                m = MagicMock()
                m.discover_datasource_uids.return_value = {}
                instances.append(m)
                return m

            MockClient.side_effect = _mk

            c1 = get_grafana_client_from_credentials(
                endpoint="https://g1.example.com",
                api_key="tok",
                account_id="acct1",
            )
            c2 = get_grafana_client_from_credentials(
                endpoint="https://g2.example.com",
                api_key="tok",
                account_id="acct1",
            )

        assert c1 is not c2
        # Two separate discoveries happened (one per endpoint)
        assert len(instances) >= 2


class TestConcurrentCacheStampede:
    """Regression tests for the race condition described in issue #4245.

    When N threads call get_grafana_client_from_credentials concurrently with
    the same (account_id, endpoint), discover_datasource_uids must be called
    exactly once — not once per thread.
    """

    def test_concurrent_callers_share_single_discovery(self) -> None:
        """N threads calling get_grafana_client_from_credentials concurrently
        for the same key must trigger discover_datasource_uids exactly once.

        Strategy: the first thread to enter _discover sleeps long enough that
        the remaining threads all arrive and queue on the per-key lock.  After
        the sleep, the lock is released, the cache is written, and the waiting
        threads all read the cached entry without calling _discover again.
        """
        import time

        discovery_call_count = 0
        count_lock = threading.Lock()
        results: list[object] = []
        errors: list[Exception] = []

        def _make_client(config):  # type: ignore[override]
            nonlocal discovery_call_count
            m = MagicMock()

            def _discover():
                nonlocal discovery_call_count
                # Sleep so sibling threads can pile up on the module-level lock.
                time.sleep(0.15)
                with count_lock:
                    discovery_call_count += 1
                return {}

            m.discover_datasource_uids.side_effect = _discover
            return m

        def _caller():
            try:
                client = get_grafana_client_from_credentials(
                    endpoint="https://g.example.com",
                    api_key="tok",
                    account_id="stampede_acct",
                )
                results.append(client)
            except Exception as exc:
                errors.append(exc)

        with patch("integrations.grafana.client.GrafanaClient", side_effect=_make_client):
            threads = [threading.Thread(target=_caller) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Threads raised exceptions: {errors}"
        assert len(results) == 8, "Not all threads returned a result"
        # Every thread must have received the same cached object
        assert len({id(r) for r in results}) == 1, (
            "Threads got different client instances — cache was not shared"
        )
        # Discovery must have run exactly once despite 8 concurrent callers
        assert discovery_call_count == 1, (
            f"discover_datasource_uids ran {discovery_call_count} times instead of 1 "
            "(cache stampede not prevented)"
        )

    def test_failed_discovery_is_still_cached(self) -> None:
        """Even when discovery fails, the client (without UIDs) is cached so
        subsequent callers don't retry the doomed network call."""
        discovery_call_count = 0

        def _make_client(config):  # type: ignore[override]
            nonlocal discovery_call_count
            m = MagicMock()

            def _discover():
                nonlocal discovery_call_count
                discovery_call_count += 1
                return {}  # empty == failure path in get_grafana_client_from_credentials

            m.discover_datasource_uids.side_effect = _discover
            return m

        with patch("integrations.grafana.client.GrafanaClient", side_effect=_make_client):
            c1 = get_grafana_client_from_credentials(
                endpoint="https://g.example.com",
                api_key="tok",
                account_id="failed_acct",
            )
            c2 = get_grafana_client_from_credentials(
                endpoint="https://g.example.com",
                api_key="tok",
                account_id="failed_acct",
            )

        assert c1 is c2
        assert discovery_call_count == 1, (
            "Failed discovery was retried on the second call — client not cached"
        )
