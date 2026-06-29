from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.background import (
    _configure_co_located_gateway_logging,
    run_poll_loop,
    telegram_gateway_auto_start_enabled,
    try_start_telegram_gateway_background,
)
from gateway.config import GatewaySettings


def test_telegram_gateway_auto_start_enabled_defaults_true() -> None:
    assert telegram_gateway_auto_start_enabled() is True


def test_telegram_gateway_auto_start_can_be_disabled(monkeypatch: object) -> None:
    monkeypatch.setenv("TELEGRAM_GATEWAY_AUTO_START", "false")  # type: ignore[attr-defined]
    assert telegram_gateway_auto_start_enabled() is False


def test_co_located_gateway_logging_does_not_propagate_to_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    _configure_co_located_gateway_logging()
    logging.getLogger("gateway.platforms.telegram.poller").warning(
        "[telegram-gateway] getUpdates not ok: {}",
    )
    assert not any("getUpdates not ok" in record.message for record in caplog.records)


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_requires_bot_token(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(bot_token="")
    assert try_start_telegram_gateway_background() is None
    mock_poll.assert_not_called()


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_skips_webhook_mode(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(
        bot_token="tok",
        webhook_url="https://example.com/hook",
        webhook_secret="secret",
    )
    assert try_start_telegram_gateway_background() is None
    mock_poll.assert_not_called()


@patch("gateway.background.run_poll_loop")
@patch("gateway.background.load_gateway_settings")
def test_try_start_starts_poll_thread(mock_settings: MagicMock, mock_poll: MagicMock) -> None:
    mock_settings.return_value = GatewaySettings(bot_token="tok")
    handle = try_start_telegram_gateway_background()
    assert handle is not None
    handle.stop(timeout=1.0)
    mock_poll.assert_called_once()


class _FakeRunner:
    """Runner whose message turn only completes once a later callback is dispatched.

    Mirrors the real deadlock shape: an approval-gated turn blocks until the
    inbound callback that approves it is processed. If the poll loop dispatches
    events sequentially with ``await``, the callback is never fetched and the
    message turn hangs forever.
    """

    def __init__(self, _settings: object = None) -> None:
        self.order: list[str] = []
        self._released: asyncio.Event | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        pass

    def clear_webhook(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def _event(self) -> asyncio.Event:
        if self._released is None:
            self._released = asyncio.Event()
        return self._released

    async def handle_inbound(self, event: str) -> None:
        if event == "message":
            self.order.append("message_start")
            await self._event().wait()
            self.order.append("message_end")
        else:
            self.order.append("callback")
            self._event().set()


class _FakePoller:
    def __init__(self, _token: str, stop_event: threading.Event) -> None:
        self._stop_event = stop_event
        self._calls = 0

    def poll_once(self) -> list[str]:
        self._calls += 1
        if self._calls == 1:
            return ["message"]
        if self._calls == 2:
            return ["callback"]
        if self._calls >= 4:
            self._stop_event.set()
        return []


def test_poll_loop_dispatches_events_concurrently() -> None:
    """An approval-gated turn must not block fetching the callback that approves it."""
    settings = GatewaySettings(bot_token="tok")
    stop_event = threading.Event()
    runner = _FakeRunner()

    with (
        patch("gateway.background.GatewayRunner", return_value=runner),
        patch(
            "gateway.background.TelegramPoller",
            side_effect=lambda token: _FakePoller(token, stop_event),
        ),
    ):
        thread = threading.Thread(target=run_poll_loop, args=(settings, stop_event), daemon=True)
        thread.start()
        thread.join(timeout=5.0)

    assert not thread.is_alive(), "poll loop deadlocked on an approval-gated turn"
    assert "callback" in runner.order
    assert "message_end" in runner.order
