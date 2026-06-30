'''
Description:
--------------------------------
The problem that I concretely need to solve now with this test
is that I do not believe that our agent has the same data access as the agent in the interactive shell 
so we need to bridge that gap between the two.

The approach to do that is:
- start the gateway and get the session
- this initializes the agent. 
- the agent is being passed down the gateway to the event handler to execute the event loops
- but during testing what we will be able to do is 
'''

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from core.agent import Agent
from gateway.config.get_gateway_settings import GatewaySettings
from gateway.start_gateway import Gateway, start_gateway


def test_gateway_start_gateway_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()

    monkeypatch.setattr("gateway.start_gateway.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.start_gateway.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.start_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr("gateway.start_gateway.signal.signal", lambda *_args: None)
    monkeypatch.setattr(
        "gateway.start_gateway.start_telegram_gateway_background",
        lambda **_kwargs: handle,
    )
    monkeypatch.setattr("core.llm.llm_client.get_llm_for_reasoning", lambda: MagicMock())

    gateway = Gateway().start_gateway(wait=False)

    assert isinstance(gateway, Gateway)
    assert gateway.settings is settings
    assert gateway.logger is logger
    assert gateway.handle is handle
    assert isinstance(gateway.agent, Agent)


def test_start_gateway_wrapper_delegates_to_gateway_instance(monkeypatch) -> None:
    expected = MagicMock(spec=Gateway)
    calls: list[bool] = []

    def _start_gateway(self: Gateway, *, wait: bool = True) -> Gateway:
        assert isinstance(self, Gateway)
        calls.append(wait)
        return expected

    monkeypatch.setattr(Gateway, "start_gateway", _start_gateway)

    assert start_gateway(wait=False) is expected
    assert calls == [False]
