"""Tests for :mod:`gateway.runtime.manager` lifecycle behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gateway.runtime.manager import GatewayManager


def test_wait_blocks_until_stop_not_telegram_thread_exit() -> None:
    """The unified daemon should not exit when the Telegram worker thread ends."""
    manager = GatewayManager()
    telegram_wait = MagicMock(return_value=True)

    class FakeTelegramWorker:
        def wait(self, *, timeout: float | None = None) -> bool:
            return telegram_wait(timeout=timeout)

        def stop(self, *, timeout: float = 8.0) -> bool:
            _ = timeout
            return True

    manager.telegram_background_worker = FakeTelegramWorker()
    manager._stopped.clear()

    assert manager.wait(timeout=0.01) is False
    telegram_wait.assert_not_called()

    manager.stop()
    assert manager.wait(timeout=0.01) is True


def test_manager_stop_never_touches_the_real_gateway_directory() -> None:
    """Stopping a manager must not clear the running daemon's status file.

    ``stop()`` calls ``clear_component_status()``, which resolves a module
    global captured at import time. Without the isolation fixture in
    ``gateway/tests/conftest.py`` this test deleted
    ``~/.opensre/gateway/components.json`` on the developer's machine.
    """
    # Arrange
    from pathlib import Path

    from gateway.runtime import daemon

    real_gateway_dir = Path.home() / ".opensre" / "gateway"

    # Act
    GatewayManager().stop()

    # Assert
    assert real_gateway_dir not in daemon.GATEWAY_COMPONENTS_FILE.parents
    assert real_gateway_dir not in daemon.GATEWAY_PID_FILE.parents


def test_manager_reload_scheduler_refreshes_component_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop/cron mutations must resync the long-lived gateway scheduler."""
    from logging import getLogger

    publishes: list[dict[str, str]] = []

    def _refresh(scheduler: object | None, *, task_filter=None):
        _ = task_filter
        assert scheduler is None
        return object(), 3

    monkeypatch.setattr(
        "platform.scheduler.runner.refresh_background_scheduler",
        _refresh,
    )
    manager = GatewayManager()
    monkeypatch.setattr(
        manager,
        "_publish_status",
        lambda _logger: publishes.append(dict(manager.components)),
    )

    manager._reload_scheduler(getLogger("test"))

    assert manager.components["scheduler"] == "running 3 scheduled task(s)"
    assert publishes == [{"scheduler": "running 3 scheduled task(s)"}]
