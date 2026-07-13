"""Tests for :mod:`gateway.manager` lifecycle behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

from gateway.manager import GatewayManager


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
