"""Datasource discovery must run once per account, not once per concurrent caller.

``get_grafana_client`` checked the cache and populated it in two separate steps
with no lock. An investigation queries Mimir, Loki, Tempo and alert rules
concurrently, so every one of them missed the empty cache and ran its own
discovery. Against a reachable Grafana that is wasted work; against an
unreachable one each attempt burns the full 10-second connect timeout and prints
its own failure line — six of them in one observed run.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from integrations.grafana import client as grafana_client


@pytest.fixture(autouse=True)
def _clear_client_cache() -> Any:
    grafana_client._grafana_client_cache.clear()
    yield
    grafana_client._grafana_client_cache.clear()


def test_concurrent_callers_discover_datasources_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten simultaneous callers must produce one discovery, not ten."""
    # Arrange
    calls: list[float] = []
    started = threading.Barrier(10)

    def _slow_failing_discovery(_self: Any) -> dict[str, str]:
        calls.append(1.0)
        # Stand in for the connect timeout against an unreachable host.
        threading.Event().wait(0.05)
        return {}

    monkeypatch.setattr(
        grafana_client.GrafanaClient, "discover_datasource_uids", _slow_failing_discovery
    )
    results: list[Any] = []
    errors: list[Exception] = []

    def _build() -> None:
        try:
            started.wait(timeout=5)
            results.append(
                grafana_client.get_grafana_client_from_credentials(
                    endpoint="http://grafana.invalid:3001", api_key="token"
                )
            )
        except Exception as exc:  # surfaced in the assert below
            errors.append(exc)

    # Act
    threads = [threading.Thread(target=_build) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    # Assert
    assert not errors, errors
    assert len(calls) == 1, f"discovery ran {len(calls)} times for one account"
    assert len({id(result) for result in results}) == 1
