"""Startup selects the configured inbound transport, and only that one."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from gateway.transports.slack import startup as startup_module
from gateway.transports.slack.settings import SlackGatewaySettings, SlackInboundTransport


def _settings(transport: SlackInboundTransport) -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        signing_secret="secret",
        inbound_transport=transport,
    )


class _StubWorker:
    def stop(self, *, _timeout: float = 8.0) -> bool:
        return True


@pytest.mark.parametrize(
    ("transport", "started", "not_started"),
    [
        (SlackInboundTransport.SOCKET_MODE, "socket", "http"),
        (SlackInboundTransport.EVENTS_API_HTTP, "http", "socket"),
    ],
)
def test_configured_transport_is_the_one_started(
    monkeypatch: pytest.MonkeyPatch,
    transport: SlackInboundTransport,
    started: str,
    not_started: str,
) -> None:
    """The other transport must not open a connection — two live consumers
    would split the Slack event stream between them."""
    # Arrange.
    calls: list[str] = []

    def _socket(**_kwargs: Any) -> _StubWorker:
        calls.append("socket")
        return _StubWorker()

    def _http(**_kwargs: Any) -> _StubWorker:
        calls.append("http")
        return _StubWorker()

    monkeypatch.setattr(startup_module, "_start_socket_mode", _socket)
    monkeypatch.setattr(startup_module, "_start_events_api_http", _http)
    monkeypatch.setattr(
        startup_module,
        "_TRANSPORT_STARTERS",
        {
            SlackInboundTransport.SOCKET_MODE: _socket,
            SlackInboundTransport.EVENTS_API_HTTP: _http,
        },
    )
    monkeypatch.setattr(startup_module, "load_slack_gateway_settings", lambda: _settings(transport))

    # Act.
    worker, settings = startup_module.start_slack_worker(
        logger=logging.getLogger("test"), handler=lambda *_a, **_k: None
    )

    # Assert.
    assert calls == [started]
    assert not_started not in calls
    assert settings.inbound_transport is transport
    assert worker is not None


def test_every_transport_has_a_starter() -> None:
    """A new enum member without a row would raise KeyError at boot."""
    # Arrange / Act / Assert.
    assert set(startup_module._TRANSPORT_STARTERS) == set(SlackInboundTransport)
