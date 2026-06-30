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
from gateway.start_gateway import Gateway, create_agent_only_gateway, start_gateway


def test_gateway_initialize_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()

    monkeypatch.setattr("gateway.start_gateway.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.start_gateway.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.start_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr("gateway.start_gateway.signal.signal", lambda *_args: None)
    monkeypatch.setattr(
        "gateway.start_gateway.create_gateway_background_handle",
        lambda _settings, **_kwargs: handle,
    )
    monkeypatch.setattr("core.agent.agent_llm_client.get_agent_llm", lambda: MagicMock())

    gateway = Gateway().initialize(wait=False)

    assert isinstance(gateway, Gateway)
    assert gateway.deps.settings is settings
    assert gateway.deps.logger is logger
    assert gateway.deps.background_handle is handle
    assert gateway.deps.agent_dependencies.repl_session is gateway.repl_session
    assert isinstance(gateway.agent, Agent)


def test_start_gateway_wrapper_delegates_to_gateway_instance(monkeypatch) -> None:
    expected = MagicMock(spec=Gateway)
    calls: list[tuple[bool, bool]] = []

    def _initialize(
        self: Gateway,
        *,
        start_polling: bool = True,
        wait: bool = True,
    ) -> Gateway:
        assert isinstance(self, Gateway)
        calls.append((start_polling, wait))
        return expected

    monkeypatch.setattr(Gateway, "initialize", _initialize)

    assert start_gateway(wait=False) is expected
    assert calls == [(True, False)]


def test_create_agent_only_gateway_initializes_without_polling(monkeypatch) -> None:
    expected = MagicMock(spec=Gateway)
    calls: list[tuple[bool, bool]] = []

    def _initialize(
        self: Gateway,
        *,
        start_polling: bool = True,
        wait: bool = True,
    ) -> Gateway:
        assert isinstance(self, Gateway)
        calls.append((start_polling, wait))
        return expected

    monkeypatch.setattr(Gateway, "initialize", _initialize)

    assert create_agent_only_gateway() is expected
    assert calls == [(False, False)]
