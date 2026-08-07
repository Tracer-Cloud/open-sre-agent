"""Tests for :mod:`gateway.core.runtime.manager` lifecycle behavior."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gateway.channels import ChannelsHandle, TransportHandle, TransportName
from gateway.core.runtime.manager import GatewayManager


def test_start_channels_delegates_to_channels_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager stores an opaque ChannelsHandle; no per-transport fields."""
    telegram = TransportHandle(
        TransportName.TELEGRAM,
        MagicMock(name="telegram"),
        "polling for messages",
    )
    expected = ChannelsHandle(
        web_server=MagicMock(name="web"),
        transports={TransportName.TELEGRAM: telegram},
        statuses={"web": "serving", TransportName.TELEGRAM: "polling for messages"},
    )
    captured: dict[str, object] = {}

    def _boot(*, logger, handler):
        captured["logger"] = logger
        captured["handler"] = handler
        return expected

    monkeypatch.setattr("gateway.core.runtime.manager.gateway_channels.start_channels", _boot)
    manager = GatewayManager()
    handler = MagicMock(name="chat-handler")
    logger = logging.getLogger("test.manager.channels")

    manager.start_channels(logger=logger, handler=handler)

    assert manager.channels is expected
    assert captured["logger"] is logger
    assert captured["handler"] is handler
    assert manager.components[TransportName.TELEGRAM] == "polling for messages"
    assert not hasattr(manager, "telegram_background_worker")
    assert not hasattr(manager, "web_server")


def test_wait_blocks_until_stop_not_channel_worker_exit() -> None:
    """The unified daemon should not exit when a chat worker thread ends."""
    manager = GatewayManager()
    worker_wait = MagicMock(return_value=True)

    class FakeWorker:
        def wait(self, *, timeout: float | None = None) -> bool:
            return worker_wait(timeout=timeout)

        def stop(self, *, timeout: float = 8.0) -> bool:
            _ = timeout
            return True

    manager.channels = ChannelsHandle(
        transports={
            TransportName.TELEGRAM: TransportHandle(
                TransportName.TELEGRAM,
                FakeWorker(),
                "polling",
            )
        }
    )
    manager._stopped.clear()

    assert manager.wait(timeout=0.01) is False
    worker_wait.assert_not_called()

    manager.stop()
    assert manager.wait(timeout=0.01) is True
    assert manager.channels is None


def test_manager_stop_never_touches_the_real_gateway_directory() -> None:
    """Stopping a manager must not clear the running daemon's status file.

    ``stop()`` calls ``clear_component_status()``, which resolves a module
    global captured at import time. Without the isolation fixture in
    ``gateway/tests/conftest.py`` this test deleted
    ``~/.opensre/gateway/components.json`` on the developer's machine.
    """
    from gateway.core.runtime import daemon

    real_gateway_dir = Path.home() / ".opensre" / "gateway"

    GatewayManager().stop()

    assert real_gateway_dir not in daemon.GATEWAY_COMPONENTS_FILE.parents
    assert real_gateway_dir not in daemon.GATEWAY_PID_FILE.parents


def test_manager_reload_scheduler_refreshes_component_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop/cron mutations must resync the long-lived gateway scheduler."""
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

    manager._reload_scheduler(logging.getLogger("test"))

    assert manager.components["scheduler"] == "running 3 scheduled task(s)"
    assert publishes == [{"scheduler": "running 3 scheduled task(s)"}]
