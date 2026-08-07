"""Characterization for the chat-transport registry in gateway.channels.chat."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from gateway.channels import chat
from gateway.channels.chat import TransportName


def test_start_transports_skips_not_configured_and_failed(
    monkeypatch,
) -> None:
    """Not configured vs failed are distinct component statuses; others still start."""
    from gateway.core.runtime.errors import (
        GatewayConfigurationError,
        GatewayTransportFailedError,
    )

    workers = {
        TransportName.TELEGRAM: MagicMock(name="telegram-worker"),
        TransportName.SLACK: MagicMock(name="slack-worker"),
        TransportName.DISCORD: MagicMock(name="discord-worker"),
    }

    def _telegram(**_kwargs):
        raise GatewayConfigurationError("TELEGRAM_BOT_TOKEN is not set")

    def _slack(**_kwargs):
        return workers[TransportName.SLACK], MagicMock(name="slack-settings")

    def _discord(**_kwargs):
        raise GatewayTransportFailedError("startup timeout")

    monkeypatch.setattr(
        chat,
        "TRANSPORTS",
        (
            chat.TransportSpec(TransportName.TELEGRAM, _telegram, "polling for messages"),
            chat.TransportSpec(TransportName.SLACK, _slack, "connected via socket mode"),
            chat.TransportSpec(TransportName.DISCORD, _discord, "connected via gateway"),
        ),
    )

    components: dict[str, str] = {}
    handles = chat.start_transports(
        logger=logging.getLogger("test.chat"),
        handler=MagicMock(),
        components=components,
    )

    assert [h.name for h in handles] == [TransportName.SLACK]
    assert handles[0].worker is workers[TransportName.SLACK]
    assert components[TransportName.TELEGRAM].startswith("not configured")
    assert components[TransportName.SLACK] == "connected via socket mode"
    assert components[TransportName.DISCORD].startswith("failed")


def test_stop_transports_stops_every_handle() -> None:
    w1 = MagicMock()
    w1.stop.return_value = True
    w2 = MagicMock()
    w2.stop.return_value = False
    handles = [
        chat.TransportHandle(TransportName.TELEGRAM, w1, MagicMock(), "polling"),
        chat.TransportHandle(TransportName.SLACK, w2, MagicMock(), "connected"),
    ]

    assert chat.stop_transports(handles, timeout=1.5) is False
    w1.stop.assert_called_once_with(timeout=1.5)
    w2.stop.assert_called_once_with(timeout=1.5)
