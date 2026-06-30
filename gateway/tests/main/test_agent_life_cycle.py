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

--------------------------------
'''

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from core.agent import Agent
from core.agent_harness.action_tools import get_action_tool
from core.agent_harness.session import ReplSession
from core.agent_harness.session.storage.memory import InMemorySessionStorage
from gateway.agent.gateway_agent_adapters import (
    GatewayErrorReporter,
    GatewayPromptContextProvider,
    GatewayRunRecordFactory,
)
from gateway.config.get_gateway_settings import GatewaySettings, TelegramInboundMessage
from gateway.polling.handle_polled_inbound_telegram_msg import (
    handle_polled_inbound_telegram_message,
)
from gateway.session.enforce_inbound_telegram_message_security import InboundDecision
from gateway.start_gateway import GatewayManager, start_gateway
from surfaces.interactive_shell.runtime.agent_harness_adapters import (
    ShellReasoningClientProvider,
    ShellToolProvider,
)


def test_gateway_start_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()
    agent = MagicMock()
    tools = [MagicMock()]
    integrations = {"shell": {"enabled": True}}
    signal_calls: list[tuple[int, Any]] = []
    background_kwargs: dict[str, Any] = {}

    class FakeReplSession:
        def __init__(self) -> None:
            self.hydrated = False

        def hydrate_configured_integrations(self) -> None:
            self.hydrated = True

        def get_integrations(self) -> SimpleNamespace:
            return SimpleNamespace(resolved_integrations=integrations)

    monkeypatch.setattr("gateway.start_gateway.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.start_gateway.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.start_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr(
        "gateway.start_gateway.signal.signal",
        lambda signum, handler: signal_calls.append((signum, handler)),
    )
    monkeypatch.setattr("gateway.start_gateway.ReplSession", FakeReplSession)
    def _get_action_tools_from_integrations_context(
        _tool_context: Any,
        *,
        resolved_integrations: dict[str, Any],
    ) -> list[MagicMock]:
        assert resolved_integrations is integrations
        return tools

    def _build_gateway_agent(
        resolved_integrations: dict[str, Any],
        discovered_tools: list[MagicMock],
    ) -> MagicMock:
        assert resolved_integrations is integrations
        assert discovered_tools is tools
        return agent

    monkeypatch.setattr(
        "gateway.start_gateway.get_action_tools_from_integrations_context",
        _get_action_tools_from_integrations_context,
    )
    monkeypatch.setattr("gateway.start_gateway.build_gateway_agent", _build_gateway_agent)

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.start_gateway.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )

    gateway = GatewayManager().start_gateway(wait=False)

    assert isinstance(gateway, GatewayManager)
    assert gateway.settings is settings
    assert gateway.logger is logger
    assert gateway.telegram_background_worker is handle
    assert gateway.agent is agent
    assert background_kwargs["settings"] is settings
    assert background_kwargs["logger"] is logger
    assert background_kwargs["handle_callback_to_gateway_agent"] is not None
    assert signal_calls
    handle.wait.assert_not_called()

    sink = MagicMock()
    session = MagicMock()
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    callback("hello", session, sink, logger)
    agent.dispatch_message_to_headless_agent.assert_called_once()
    dispatch_args = agent.dispatch_message_to_headless_agent.call_args
    assert dispatch_args.args == ("hello",)
    assert dispatch_args.kwargs["session"] is session
    assert dispatch_args.kwargs["output"] is sink
    tool_provider = dispatch_args.kwargs["tools"]
    assert isinstance(tool_provider, ShellToolProvider)
    assert tool_provider.action_tools(confirm_fn=None, is_tty=False) == tools
    assert isinstance(dispatch_args.kwargs["prompts"], GatewayPromptContextProvider)
    assert isinstance(dispatch_args.kwargs["reasoning"], ShellReasoningClientProvider)
    assert isinstance(dispatch_args.kwargs["run_factory"], GatewayRunRecordFactory)
    assert isinstance(dispatch_args.kwargs["error_reporter"], GatewayErrorReporter)
    assert dispatch_args.kwargs["gather_enabled"] is True


def test_polled_telegram_message_reaches_start_gateway_agent_callback(monkeypatch) -> None:
    settings = GatewaySettings(
        bot_token="tok",
        auto_start_enabled=False,
        allowed_user_ids=["user-1"],
        stream_edit_interval_seconds=0.01,
    )
    logger = logging.getLogger("gateway.lifecycle.e2e.test")
    handle = MagicMock()
    slash_tool = get_action_tool("slash_invoke")
    assert slash_tool is not None
    tools: list[Any] = [slash_tool]
    integrations: dict[str, Any] = {}
    background_kwargs: dict[str, Any] = {}

    class FakeBootReplSession:
        def hydrate_configured_integrations(self) -> None:
            return None

        def get_integrations(self) -> SimpleNamespace:
            return SimpleNamespace(resolved_integrations=integrations)

    class FakeSessionResolver:
        def __init__(self, session: ReplSession) -> None:
            self._session = session

        def resolve(self, *, user_id: str, chat_id: str) -> ReplSession:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

        def rotate(self, *, user_id: str, chat_id: str) -> ReplSession:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

    monkeypatch.setattr("gateway.start_gateway.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.start_gateway.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.start_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr("gateway.start_gateway.signal.signal", lambda *_args: None)
    monkeypatch.setattr("gateway.start_gateway.ReplSession", FakeBootReplSession)
    monkeypatch.setattr(
        "gateway.start_gateway.get_action_tools_from_integrations_context",
        lambda *_args, **_kwargs: tools,
    )
    monkeypatch.setattr(
        "gateway.start_gateway.build_gateway_agent",
        lambda *_args, **_kwargs: Agent(
            system="gateway test",
            tools=tools,
            resolved_integrations=integrations,
            max_iterations=1,
        ),
    )

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.start_gateway.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )
    monkeypatch.setattr(
        "gateway.polling.handle_polled_inbound_telegram_msg."
        "enforce_inbound_telegram_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )

    GatewayManager().start_gateway(wait=False)
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    session = ReplSession(storage=InMemorySessionStorage())
    client = MagicMock()
    client.send_message.return_value = (True, "", "message-1")
    client.edit_message_text.return_value = (True, "")

    async def _run_message() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            await handle_polled_inbound_telegram_message(
                TelegramInboundMessage(
                    update_id=1,
                    user_id="user-1",
                    chat_id="chat-1",
                    message_id="telegram-message-1",
                    text="/status",
                ),
                client=client,
                session_resolver=FakeSessionResolver(session),
                settings=settings,
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                handle_callback_to_gateway_agent=callback,
            )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(_run_message())
    client.send_chat_action.assert_called_once_with("chat-1", "typing")


def test_start_gateway_wrapper_delegates_to_gateway_instance(monkeypatch) -> None:
    expected = MagicMock(spec=GatewayManager)
    calls: list[bool] = []

    def _start_gateway(
        self: GatewayManager,
        *,
        wait: bool = True,
    ) -> GatewayManager:
        assert isinstance(self, GatewayManager)
        calls.append(wait)
        return expected

    monkeypatch.setattr(GatewayManager, "start_gateway", _start_gateway)

    assert start_gateway(wait=False) is expected
    assert calls == [False]


