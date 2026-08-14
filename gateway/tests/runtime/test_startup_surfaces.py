"""Characterization for :mod:`gateway.startup` — web + chat as one unit."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from gateway.core.transport_api import TransportName
from gateway.startup import StartedGateway, start_gateway
from gateway.transports.startup import ChatStartup, TransportHandle
from gateway.web.startup import WebStartup


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


def test_channels_handle_stop_stops_web_and_transports() -> None:
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
    w1.stop.assert_called_once_with(timeout=1.5)
    w2.stop.assert_called_once_with(timeout=1.5)
    assert handle.web_server is None
    assert handle.transports == {}
