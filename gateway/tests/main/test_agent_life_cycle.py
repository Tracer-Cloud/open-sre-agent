"""
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
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.models.turn_results import ShellTurnResult, ToolCallingTurnResult
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.providers.default_providers import (
    DefaultErrorReporter,
    DefaultReasoningClientProvider,
    DefaultRunRecordFactory,
    DefaultToolProvider,
    DefaultTurnAccounting,
)
from core.agent_harness.session import Session
from core.agent_harness.session.storage.memory import InMemorySessionStorage
from gateway.config.get_gateway_settings import GatewaySettings, TelegramInboundMessage
from gateway.manager import GatewayManager, start_gateway
from gateway.polling.handle_polled_inbound_telegram_msg import (
    handle_polled_inbound_telegram_message,
)
from gateway.session.enforce_inbound_telegram_message_security import InboundDecision


def test_gateway_start_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()
    dispatch = MagicMock()
    signal_calls: list[tuple[int, Any]] = []
    background_kwargs: dict[str, Any] = {}

    monkeypatch.setattr("core.agent_harness.harness.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.manager.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.telegram_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr(
        "gateway.manager.signal.signal",
        lambda signum, handler: signal_calls.append((signum, handler)),
    )
    # Dispatch is a static entry point; patch it on the class to spy the callback.
    monkeypatch.setattr("gateway.turn_handler.dispatch_message_to_headless_agent", dispatch)

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.telegram_gateway.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )

    gateway = GatewayManager().start_gateway(wait=False)

    assert isinstance(gateway, GatewayManager)
    assert gateway.settings is settings
    assert gateway.logger is logger
    assert gateway.telegram_background_worker is handle
    assert background_kwargs["settings"] is settings
    assert background_kwargs["logger"] is logger
    assert background_kwargs["handle_callback_to_gateway_agent"] is not None
    assert signal_calls
    handle.wait.assert_not_called()

    sink = MagicMock()
    session = MagicMock()
    dispatch.return_value = ShellTurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="Hawaii: +25C",
        ),
        assistant_response_text="Hawaii: +25C",
        llm_run=None,
    )
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    callback("hello", session, sink, logger)
    dispatch.assert_called_once()
    sink.finalize.assert_called_once_with("Hawaii: +25C")
    dispatch_args = dispatch.call_args
    assert dispatch_args.args == ("hello",)
    assert dispatch_args.kwargs["session"] is session
    assert dispatch_args.kwargs["output"] is sink
    tool_provider = dispatch_args.kwargs["tools"]
    assert isinstance(tool_provider, DefaultToolProvider)
    assert tool_provider._precomputed_action_tools is None
    with patch.object(logger, "info") as mock_info:
        tool_provider.observer(message="hello")(
            "tool_start",
            {"name": "shell_run", "input": {"command": "pwd"}},
        )
    mock_info.assert_called_once_with(
        "tool action name=%s input=%s",
        "shell_run",
        "{'command': 'pwd'}",
    )
    assert isinstance(dispatch_args.kwargs["prompts"], DefaultPromptContextProvider)
    assert isinstance(dispatch_args.kwargs["reasoning"], DefaultReasoningClientProvider)
    assert isinstance(dispatch_args.kwargs["run_factory"], DefaultRunRecordFactory)
    assert isinstance(dispatch_args.kwargs["accounting"], DefaultTurnAccounting)
    assert isinstance(dispatch_args.kwargs["error_reporter"], DefaultErrorReporter)
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
    background_kwargs: dict[str, Any] = {}

    class FakeSessionResolver:
        def __init__(self, session: Session) -> None:
            self._session = session

        def resolve(self, *, user_id: str, chat_id: str) -> Session:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

        def rotate(self, *, user_id: str, chat_id: str) -> Session:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

    monkeypatch.setattr("core.agent_harness.harness.load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr("gateway.manager.configure_gateway_logging", lambda: logger)
    monkeypatch.setattr("gateway.telegram_gateway.load_gateway_settings", lambda: settings)
    monkeypatch.setattr("gateway.manager.signal.signal", lambda *_args: None)

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.telegram_gateway.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )
    monkeypatch.setattr(
        "gateway.polling.handle_polled_inbound_telegram_msg."
        "enforce_inbound_telegram_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )

    GatewayManager().start_gateway(wait=False)
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    session = Session(storage=InMemorySessionStorage())
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
