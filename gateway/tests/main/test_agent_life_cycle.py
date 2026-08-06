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

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from gateway.core.runtime.manager import GatewayManager, start_gateway
from gateway.transports.telegram.inbound_handler import (
    handle_polled_inbound_telegram_message,
)
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.settings import (
    GatewayConfigurationError,
    GatewaySettings,
    TelegramInboundMessage,
)


def _patch_non_telegram_components(monkeypatch) -> None:
    """Keep lifecycle tests focused on the Telegram worker: skip web/scheduler/pidfile."""
    monkeypatch.setattr(GatewayManager, "_start_web", lambda *_args: None)
    monkeypatch.setattr(GatewayManager, "_start_scheduler", lambda *_args: None)
    monkeypatch.setattr(GatewayManager, "_publish_status", lambda *_args: None)


def _patch_process_boot(monkeypatch) -> None:
    """Neutralize shared bootstrap.process side effects during lifecycle tests."""
    from bootstrap.process import reset_process_runtime_for_tests

    reset_process_runtime_for_tests()
    monkeypatch.setattr(
        "bootstrap.process.bootstrap_opensre_env_once",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("bootstrap.process.install_harness_adapters", lambda: None)
    monkeypatch.setattr("bootstrap.process.install_scheduler_runners", lambda: None)
    monkeypatch.setattr(
        "platform.observability.errors.sentry.init_sentry",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("core.llm.internal.preload.preload_llm_clients", lambda: None)
    monkeypatch.setattr(
        "platform.sandbox.capabilities.boot_capability_warnings",
        lambda: [],
    )


def test_gateway_start_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()
    agent_cls = MagicMock()
    signal_calls: list[tuple[int, Any]] = []
    background_kwargs: dict[str, Any] = {}

    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.runtime.manager.configure_logging", lambda: logger)
    _patch_non_telegram_components(monkeypatch)
    monkeypatch.setattr(
        "gateway.transports.telegram.wiring.load_gateway_settings", lambda: settings
    )
    monkeypatch.setattr(
        "gateway.core.runtime.manager.signal.signal",
        lambda signum, handler: signal_calls.append((signum, handler)),
    )
    # Patch the agent factory the gateway uses so the turn callback is spyable.
    monkeypatch.setattr(
        "gateway.core.runtime.session_agents.build_default_headless_agent",
        agent_cls,
    )

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.transports.telegram.wiring.start_telegram_gateway_background",
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
    agent_cls.return_value.dispatch.return_value = TurnResult(
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
    agent_cls.return_value.dispatch.assert_called_once()
    sink.finalize.assert_called_once_with("Hawaii: +25C")
    assert agent_cls.return_value.dispatch.call_args.args == ("hello",)
    ctor = agent_cls.call_args
    assert ctor.kwargs["session"] is session
    # Session-scoped agent holds a live sink proxy; the transport sink is bound
    # each turn (not passed as the constructor ``output`` identity).
    from gateway.core.runtime.live_sink import LiveOutputSink

    assert isinstance(ctor.kwargs["output"], LiveOutputSink)
    assert ctor.kwargs["surface"] == "gateway"
    from core.agent_harness.turns.gather_ports import GatherPorts

    assert isinstance(ctor.kwargs["gather"], GatherPorts)
    assert ctor.kwargs["gather"].enabled is True
    assert ctor.kwargs["is_tty"] is False
    assert ctor.kwargs["observer_factory"] is not None
    tool_provider = DefaultToolProvider(
        ctor.kwargs["session"],
        ctor.kwargs["console"],
        tool_action_logger=ctor.kwargs["logger"],
        observer_factory=ctor.kwargs["observer_factory"],
        subprocess_presenter_factory=ctor.kwargs.get("subprocess_presenter_factory"),
        slash_ports_factory=ctor.kwargs.get("slash_ports_factory"),
    )
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
    bind_kwargs = agent_cls.return_value.bind_turn.call_args.kwargs
    assert isinstance(bind_kwargs["accounting"], DefaultTurnAccounting)


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
        def __init__(self, session: SessionCore) -> None:
            self._session = session

        def resolve(self, *, user_id: str, chat_id: str) -> SessionCore:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

        def rotate(self, *, user_id: str, chat_id: str) -> SessionCore:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.runtime.manager.configure_logging", lambda: logger)
    _patch_non_telegram_components(monkeypatch)
    monkeypatch.setattr(
        "gateway.transports.telegram.wiring.load_gateway_settings", lambda: settings
    )
    monkeypatch.setattr("gateway.core.runtime.manager.signal.signal", lambda *_args: None)

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.transports.telegram.wiring.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )
    monkeypatch.setattr(
        "gateway.transports.telegram.inbound_handler.enforce_inbound_telegram_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )

    GatewayManager().start_gateway(wait=False)
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    session = SessionCore(storage=InMemorySessionStorage())
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
    # Typing fires at sink creation and again on each status refresh.
    client.send_chat_action.assert_called_with("chat-1", "typing")


def test_gateway_start_continues_without_telegram_configuration(monkeypatch) -> None:
    """The unified daemon keeps its other components when Telegram is unconfigured."""
    logger = logging.getLogger("gateway.lifecycle.test")
    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.runtime.manager.configure_logging", lambda: logger)
    monkeypatch.setattr("gateway.core.runtime.manager.signal.signal", lambda *_args: None)
    monkeypatch.setattr("gateway.core.runtime.manager.clear_component_status", lambda: None)
    _patch_non_telegram_components(monkeypatch)

    def _unconfigured() -> GatewaySettings:
        raise GatewayConfigurationError("TELEGRAM_BOT_TOKEN is not set")

    monkeypatch.setattr("gateway.transports.telegram.wiring.load_gateway_settings", _unconfigured)

    gateway = GatewayManager().start_gateway(wait=False)

    assert gateway.telegram_background_worker is None
    assert gateway.components["telegram"].startswith("not configured")
    assert gateway.stop() is True


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
