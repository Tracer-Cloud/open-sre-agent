"""Characterization for :mod:`gateway.startup` — web + chat as one unit."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import WEB_STOP_TIMEOUT_SECONDS
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.startup import StartedGateway, start_gateway
from gateway.transports.names import TransportName
from gateway.transports.startup import ChatStartup, TransportHandle
from gateway.web.startup import WebStartup


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_start_gateway_boots_web_and_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server = MagicMock(name="web")
    telegram_worker = MagicMock(name="telegram")
    handles = [
        TransportHandle(
            TransportName.TELEGRAM,
            telegram_worker,
            "polling for messages",
        ),
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "gateway.startup.start_web_server",
        lambda **kwargs: captured.update(kwargs) or WebStartup(server=web_server, status="serving"),
    )

    def _start_transports(*, logger, handler):
        captured["logger"] = logger
        captured["handler"] = handler
        return ChatStartup(
            handles=handles,
            statuses={TransportName.TELEGRAM: "polling for messages"},
        )

    monkeypatch.setattr("gateway.startup.start_transports", _start_transports)

    logger = logging.getLogger("test.channels")
    handler = MagicMock(name="chat-handler")
    channels = start_gateway(logger=logger, handler=handler)

    assert channels.web_server is web_server
    assert channels.transports[TransportName.TELEGRAM] is handles[0]
    assert channels.transports[TransportName.TELEGRAM].worker is telegram_worker
    assert TransportName.SLACK not in channels.transports
    assert channels.statuses["web"] == "serving"
    assert channels.statuses[TransportName.TELEGRAM] == "polling for messages"
    assert captured["logger"] is logger
    assert captured["handler"] is handler


def test_channels_handle_stop_stops_web_and_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Freeze the shutdown clock: on a loaded CI runner, real time elapsing
    # between budget creation and the worker stop call shrinks the remaining
    # timeout below the asserted 1.5s.
    clock = _Clock()

    def _budget(seconds: float) -> ShutdownBudget:
        return ShutdownBudget(seconds, clock=clock)

    monkeypatch.setattr("gateway.startup.ShutdownBudget", _budget)
    web = MagicMock()
    w1 = MagicMock()
    w1.stop.return_value = True
    w2 = MagicMock()
    w2.stop.return_value = False
    handle = StartedGateway(
        web_server=web,
        transports={
            TransportName.TELEGRAM: TransportHandle(TransportName.TELEGRAM, w1, "polling"),
            TransportName.SLACK: TransportHandle(TransportName.SLACK, w2, "connected"),
        },
    )

    assert handle.stop(timeout=1.5) is False
    web.stop.assert_called_once()
    assert w1.stop.call_args.kwargs["timeout"] == pytest.approx(1.5, abs=0.05)
    assert w2.stop.call_args.kwargs["timeout"] == pytest.approx(1.5, abs=0.05)
    assert handle.web_server is None
    assert handle.transports == {}


def test_web_stop_leaves_remaining_budget_for_chat_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web join is a slice of the SIGTERM budget, not extra time on top of it."""
    clock = _Clock()

    def _budget(seconds: float) -> ShutdownBudget:
        return ShutdownBudget(seconds, clock=clock)

    monkeypatch.setattr("gateway.startup.ShutdownBudget", _budget)
    web = MagicMock()

    def _web_stop(*, timeout: float) -> None:
        _ = timeout
        clock.now += 3.0

    web.stop.side_effect = _web_stop
    worker = MagicMock()
    worker.stop.return_value = True
    handle = StartedGateway(
        web_server=web,
        transports={
            TransportName.TELEGRAM: TransportHandle(TransportName.TELEGRAM, worker, "polling"),
        },
    )

    assert handle.stop(timeout=8.0) is True
    web.stop.assert_called_once_with(timeout=WEB_STOP_TIMEOUT_SECONDS)
    worker.stop.assert_called_once_with(timeout=5.0)
