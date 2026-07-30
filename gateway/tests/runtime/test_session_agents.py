"""Session-scoped agent pool and live sink reuse across turns."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.turns.turn_results import ShellTurnResult, ToolCallingTurnResult
from gateway.runtime.live_sink import LiveOutputSink
from gateway.runtime.session_agents import SessionAgentPool
from gateway.runtime.turn_handler import GatewayTurnHandler


@pytest.fixture(autouse=True)
def _stub_gateway_turn_analytics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gateway.runtime.turn_handler.capture_gateway_turn_started", lambda **_: None
    )
    monkeypatch.setattr(
        "gateway.runtime.turn_handler.capture_gateway_turn_completed", lambda **_: None
    )
    monkeypatch.setattr(
        "gateway.runtime.turn_handler.capture_gateway_turn_failed", lambda **_: None
    )


def _empty_result() -> ShellTurnResult:
    return ShellTurnResult(
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
    )


def test_live_sink_requires_bind_before_use() -> None:
    sink = LiveOutputSink()
    with pytest.raises(RuntimeError, match="not bound"):
        sink.finalize("x")


def test_live_sink_rebinds_across_turns() -> None:
    live = LiveOutputSink()
    first = MagicMock()
    second = MagicMock()
    live.bind(first)
    live.finalize("a")
    first.finalize.assert_called_once_with("a")
    live.bind(second)
    live.set_tool_status("running")
    second.set_tool_status.assert_called_once_with("running")


def test_pool_reuses_agent_for_same_session(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[Any] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()
            self.dispatch = MagicMock(return_value=_empty_result())

    def _fake_build(**kwargs: Any) -> Any:
        agent = _FakeAgent(**kwargs)
        return agent

    monkeypatch.setattr(
        "gateway.runtime.session_agents.build_default_headless_agent",
        _fake_build,
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    session = SessionCore(storage=InMemorySessionStorage())
    logger = logging.getLogger("test.pool")
    first = pool.agent_for(session=session, sink=MagicMock(), logger=logger)
    second = pool.agent_for(session=session, sink=MagicMock(), logger=logger)
    assert first is second
    assert len(constructed) == 1
    assert session.session_id in pool.cached_session_ids


def test_pool_builds_separate_agents_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()

    monkeypatch.setattr(
        "gateway.runtime.session_agents.build_default_headless_agent",
        lambda **kwargs: _FakeAgent(**kwargs),
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    a = SessionCore(storage=InMemorySessionStorage())
    b = SessionCore(storage=InMemorySessionStorage())
    logger = logging.getLogger("test.pool")
    agent_a = pool.agent_for(session=a, sink=MagicMock(), logger=logger)
    agent_b = pool.agent_for(session=b, sink=MagicMock(), logger=logger)
    assert agent_a is not agent_b
    assert pool.cached_session_ids == frozenset({a.session_id, b.session_id})


def test_pool_rebinds_current_session_on_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway resolve() yields a new SessionCore each turn; reuse must follow it."""
    bound: list[Any] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.session = kwargs["session"]
            self.bind_turn = MagicMock()

        def bind_session(self, session: Any) -> None:
            bound.append(session)
            self.session = session

    monkeypatch.setattr(
        "gateway.runtime.session_agents.build_default_headless_agent",
        lambda **kwargs: _FakeAgent(**kwargs),
    )
    pool = SessionAgentPool(console=Console(force_terminal=False))
    first = SessionCore(storage=InMemorySessionStorage())
    # Same logical id, different object — what SessionManager.resolve returns.
    second = SessionCore(storage=InMemorySessionStorage(), session_id=first.session_id)
    logger = logging.getLogger("test.pool.rebind")
    agent_a = pool.agent_for(session=first, sink=MagicMock(), logger=logger)
    agent_b = pool.agent_for(session=second, sink=MagicMock(), logger=logger)
    assert agent_a is agent_b
    assert bound == [second]
    assert agent_b.session is second


def test_turn_handler_reuses_headless_agent_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = MagicMock()
    agent.dispatch.return_value = _empty_result()
    factory = MagicMock(return_value=agent)
    monkeypatch.setattr(
        "gateway.runtime.session_agents.build_default_headless_agent",
        factory,
    )

    session = SessionCore(storage=InMemorySessionStorage())
    handler = GatewayTurnHandler(console=Console(force_terminal=False))
    logger = logging.getLogger("test.reuse")
    handler("one", session, MagicMock(), logger)
    handler("two", session, MagicMock(), logger)

    assert factory.call_count == 1
    assert agent.dispatch.call_count == 2
    assert agent.bind_turn.call_count == 2
