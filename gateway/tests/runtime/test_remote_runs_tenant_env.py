"""``_start_remote_runs`` identifies the tenant from the injected env var.

This path fails silently: a blank organization id does not raise, it marks
``api_runs`` "not configured" and returns, so a silo whose runs never execute
looks healthy. Reading the wrong env name here is therefore invisible without a
test that pins which name is read.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.tenancy import TENANT_ORGANIZATION_ID_ENV
from gateway.runtime.credential_hydration import GatewayBootstrap
from gateway.runtime.manager import GatewayManager

_DSN = "postgresql://user:pw@neon.example/db"


class _RecordingWorker:
    """Captures the organization id the manager passed to the factory."""

    def __init__(self, organization_id: str) -> None:
        self.organization_id = organization_id
        self.started = False

    def start(self) -> None:
        self.started = True


def _recording_factory(built: list[_RecordingWorker]) -> Any:
    def factory(org: str, _dsn: str, _handler: Any, _gate: Any, _log: Any) -> _RecordingWorker:
        worker = _RecordingWorker(org)
        built.append(worker)
        return worker

    return factory


@pytest.fixture(autouse=True)
def _clear_org_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither organization env var is set unless a test sets it."""
    for name in (TENANT_ORGANIZATION_ID_ENV, ORGANIZATION_ID_ENV):
        monkeypatch.delenv(name, raising=False)


def _start(manager: GatewayManager) -> None:
    manager._start_remote_runs(
        logging.getLogger("test"),
        object(),
        GatewayBootstrap(database_url=_DSN),
    )


def test_worker_starts_with_the_injected_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tenant id ECS injects reaches the worker, and the worker starts."""
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org-from-control-plane")
    built: list[_RecordingWorker] = []
    manager = GatewayManager(remote_run_worker_factory=_recording_factory(built))

    # Act
    _start(manager)

    # Assert
    assert [w.organization_id for w in built] == ["org-from-control-plane"]
    assert built[0].started is True
    assert manager.components["api_runs"] == "polling"


def test_billing_org_id_does_not_start_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The billing var must not stand in for the tenant id.

    This is the regression: the manager read ``OPENSRE_ORGANIZATION_ID``, which a
    silo does not set, so the worker never started and nothing said so.
    """
    # Arrange: only the billing name is set, with a value that must not leak.
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-billing-must-not-leak")
    built: list[_RecordingWorker] = []
    manager = GatewayManager(remote_run_worker_factory=_recording_factory(built))

    # Act
    _start(manager)

    # Assert
    assert built == []
    assert manager.remote_run_worker is None
    assert manager.components["api_runs"] == "not configured"


def test_blank_tenant_id_leaves_the_worker_unstarted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace is not an identity."""
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "   ")
    built: list[_RecordingWorker] = []
    manager = GatewayManager(remote_run_worker_factory=_recording_factory(built))

    # Act
    _start(manager)

    # Assert
    assert built == []
    assert manager.components["api_runs"] == "not configured"


def test_tenant_id_without_a_database_url_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote runs need both the tenant identity and the bootstrap DSN."""
    # Arrange
    monkeypatch.setenv(TENANT_ORGANIZATION_ID_ENV, "org-from-control-plane")
    built: list[_RecordingWorker] = []
    manager = GatewayManager(remote_run_worker_factory=_recording_factory(built))

    # Act
    manager._start_remote_runs(logging.getLogger("test"), object(), GatewayBootstrap())

    # Assert
    assert built == []
    assert manager.components["api_runs"] == "not configured"
