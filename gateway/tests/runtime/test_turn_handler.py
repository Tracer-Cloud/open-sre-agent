"""Tests for gateway turn handler wiring."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from gateway.core.runtime.turn_handler import GatewayTurnHandler
from tests.core.agent.orchestration.cross_surface_parity_harness import (
    RecordingGatewaySink,
)


@pytest.fixture(autouse=True)
def _stub_gateway_turn_analytics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gateway.core.runtime.turn_handler.capture_gateway_turn_started", lambda **_: None
    )
    monkeypatch.setattr(
        "gateway.core.runtime.turn_handler.capture_gateway_turn_completed", lambda **_: None
    )
    monkeypatch.setattr(
        "gateway.core.runtime.turn_handler.capture_gateway_turn_failed", lambda **_: None
    )


def _patch_headless_agent(monkeypatch: Any, result: TurnResult) -> MagicMock:
    """Patch the gateway agent factory so construction is inert and dispatch returns ``result``.

    Returns the factory mock. The built agent is ``factory.return_value``; when the
    test needs the real tool provider, read ``factory.return_value.tools_for_test``.
    """
    from core.agent_harness.tools.tool_provider import DefaultToolProvider

    agent = MagicMock()
    agent.dispatch.return_value = result
    factory = MagicMock()

    def _build(**kwargs: Any) -> MagicMock:
        agent.tools_for_test = DefaultToolProvider(
            kwargs["session"],
            kwargs["console"],
            tool_action_logger=kwargs.get("logger"),
            observer_factory=kwargs.get("observer_factory"),
            subprocess_presenter_factory=kwargs.get("subprocess_presenter_factory"),
            slash_ports_factory=kwargs.get("slash_ports_factory"),
        )
        return agent

    factory.side_effect = _build
    factory.return_value = agent
    monkeypatch.setattr(
        "gateway.core.runtime.session_agents.build_default_headless_agent",
        factory,
    )
    return factory


def test_turn_handler_resolves_action_tools_from_live_session(monkeypatch: Any) -> None:
    """Per-chat session integrations must drive the action tool list each turn.

    Precomputing tools at gateway boot (from an empty boot session) left the
    action agent with no integration-scoped tools, so ``run_turn`` fell through
    to the answer CLI agent on Telegram while the shell worked.
    """
    recorded: list[dict[str, Any] | None] = []

    def _fake_get_tools(
        _ctx: Any,
        *,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[Any]:
        recorded.append(resolved_integrations)
        return [MagicMock(name="slack_send_message")]

    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_context",
        _fake_get_tools,
    )

    agent_cls = _patch_headless_agent(
        monkeypatch,
        TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
        ),
    )

    session = SessionCore(storage=InMemorySessionStorage())
    chat_integrations = {"slack": {"webhook_url": "https://hooks.example/test"}}
    session.resolved_integrations_cache = chat_integrations

    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("send slack update", session, MagicMock(), logging.getLogger("test.turn_handler"))

    tool_provider = agent_cls.return_value.tools_for_test
    tools = tool_provider.action_tools(confirm_fn=None, is_tty=False)
    assert len(tools) == 1
    assert recorded == [chat_integrations]


def _empty_turn_result(*, llm_run: Any = None) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="",
        ),
        assistant_response_text="",
        llm_run=llm_run,
    )


def test_turn_handler_finalizes_fallback_on_empty_response(monkeypatch: Any) -> None:
    """An empty, non-answered turn still finalizes so the placeholder status can't hang."""
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("/", SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    sink.finalize.assert_called_once_with("I didn't have anything to add for that.")


def test_turn_handler_skips_finalize_when_answer_was_streamed(monkeypatch: Any) -> None:
    """A streamed answer (llm_run set) already resolved the status; do not re-finalize."""
    result = _empty_turn_result(llm_run=MagicMock())  # answered=True
    _patch_headless_agent(monkeypatch, result)
    sink = MagicMock()
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("hi", SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    sink.finalize.assert_not_called()


def test_turn_handler_forwards_sink_tool_hooks_to_agent(monkeypatch: Any) -> None:
    """A sink carrying tool hooks (Slack's approval gate) rebinds them each turn."""
    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    sink = MagicMock()
    hooks = object()
    sink.tool_hooks = hooks
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler("hi", SessionCore(storage=InMemorySessionStorage()), sink, logging.getLogger("test"))
    agent = agent_cls.return_value
    assert agent.bind_turn.call_args.kwargs["tool_hooks"] is hooks


def test_turn_handler_tolerates_sinks_without_tool_hooks(monkeypatch: Any) -> None:
    """Sinks without the attribute (Telegram) run unhooked, as before."""

    class _BareSink:
        def finalize(self, text: str) -> None:
            self.finalized = text

    agent_cls = _patch_headless_agent(monkeypatch, _empty_turn_result())
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler(
        "hi", SessionCore(storage=InMemorySessionStorage()), _BareSink(), logging.getLogger("test")
    )
    agent = agent_cls.return_value
    assert agent.bind_turn.call_args.kwargs["tool_hooks"] is None


def test_turn_handler_disables_unsupported_gateway_capabilities() -> None:
    session = SessionCore(storage=InMemorySessionStorage())
    handler = GatewayTurnHandler(console=Console(force_terminal=False))

    handler(
        "hello",
        session,
        RecordingGatewaySink(),
        logging.getLogger("test"),
    )

    assert session.available_capabilities["investigation"] == ()
    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()


def test_turn_handler_preserves_supported_capabilities() -> None:
    session = SessionCore(storage=InMemorySessionStorage())
    session.available_capabilities.update(
        {
            "investigation": ("existing-investigation",),
            "llm_provider": ("existing-provider",),
            "task_cancel": ("existing-cancel",),
            "shell_commands": ("shell",),
            "custom_gateway_capability": ("enabled",),
        }
    )

    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    handler(
        "hello",
        session,
        RecordingGatewaySink(),
        logging.getLogger("test.gateway.capabilities"),
    )

    assert session.available_capabilities["investigation"] == ()
    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()

    assert session.available_capabilities["shell_commands"] == ("shell",)
    assert session.available_capabilities["custom_gateway_capability"] == ("enabled",)


def test_turn_handler_capability_gating_is_stable_across_turns() -> None:
    session = SessionCore(storage=InMemorySessionStorage())
    session.available_capabilities["shell_commands"] = ("shell",)

    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    logger = logging.getLogger("test.gateway.capabilities")

    handler("first turn", session, RecordingGatewaySink(), logger)
    handler("second turn", session, RecordingGatewaySink(), logger)

    assert session.available_capabilities["investigation"] == ()
    assert session.available_capabilities["llm_provider"] == ()
    assert session.available_capabilities["task_cancel"] == ()
    assert session.available_capabilities["shell_commands"] == ("shell",)


def test_turn_handler_emits_gateway_turn_analytics(monkeypatch: Any) -> None:
    started: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []

    monkeypatch.setattr(
        "gateway.core.runtime.turn_handler.capture_gateway_turn_started",
        lambda **kwargs: started.append(kwargs),
    )
    monkeypatch.setattr(
        "gateway.core.runtime.turn_handler.capture_gateway_turn_completed",
        lambda **kwargs: completed.append(kwargs),
    )
    _patch_headless_agent(monkeypatch, _empty_turn_result())

    from platform.analytics.usage_context import SURFACE_SLACK, bound_usage_context

    session = SessionCore(storage=InMemorySessionStorage())
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    with bound_usage_context(surface=SURFACE_SLACK, user_id="U1"):
        handler("hi", session, MagicMock(), logging.getLogger("test"))

    assert started == [{"surface": SURFACE_SLACK}]
    assert len(completed) == 1
    assert completed[0]["surface"] == SURFACE_SLACK
    assert completed[0]["answered"] is False


def test_turn_handler_holds_the_session_lock_for_the_whole_turn(monkeypatch: Any) -> None:
    """The handler must take the pool's lock, not the unsynchronised primitive.

    ``session_agent`` holds the per-session lock across dispatch. Calling
    ``agent_for`` directly returns an unguarded agent, so an overlapping turn
    for the same session can rebind its session and sink mid-dispatch and route
    output to the wrong conversation.
    """
    # Arrange: record when the lock is held relative to the dispatch.
    _patch_headless_agent(monkeypatch, _empty_turn_result())
    events: list[str] = []
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    real_session_agent = handler._pool.session_agent

    @contextmanager
    def _tracking_session_agent(**kwargs: Any) -> Any:
        events.append("lock-acquired")
        with real_session_agent(**kwargs) as agent:
            yield agent
        events.append("lock-released")

    monkeypatch.setattr(handler._pool, "session_agent", _tracking_session_agent)

    # Act
    handler(
        "hi", SessionCore(storage=InMemorySessionStorage()), MagicMock(), logging.getLogger("test")
    )

    # Assert: the turn ran inside the lock. Calling agent_for directly would
    # leave this empty, since session_agent would never be entered.
    assert events == ["lock-acquired", "lock-released"]
