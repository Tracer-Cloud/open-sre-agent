"""Session-scoped agent pool and live sink reuse across turns."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from gateway.core.runtime.live_sink import LiveOutputSink
from gateway.core.runtime.session_agents import SessionAgentPool
from gateway.core.runtime.turn_handler import GatewayTurnHandler


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


def _empty_result() -> TurnResult:
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
        "gateway.core.runtime.session_agents.build_default_headless_agent",
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
        "gateway.core.runtime.session_agents.build_default_headless_agent",
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
        "gateway.core.runtime.session_agents.build_default_headless_agent",
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
        "gateway.core.runtime.session_agents.build_default_headless_agent",
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


def _fake_agent_pool(monkeypatch: pytest.MonkeyPatch) -> SessionAgentPool:
    """Pool whose agents are stubs — these tests exercise locking, not dispatch."""

    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            self.bind_turn = MagicMock()
            self.bind_session = MagicMock()

    monkeypatch.setattr(
        "gateway.core.runtime.session_agents.build_default_headless_agent",
        lambda **kwargs: _FakeAgent(**kwargs),
    )
    return SessionAgentPool(console=Console(force_terminal=False))


def test_same_session_turns_do_not_interleave(monkeypatch: pytest.MonkeyPatch) -> None:
    """One agent per session is shared, so turns for it must not overlap.

    ``agent_for`` rebinds the cached agent's session and live sink. If a second
    turn for the same session rebinds while the first is still dispatching, the
    first turn's remaining output goes to the second turn's sink — a reply
    delivered to the wrong conversation.
    """
    # Arrange: the first turn holds the agent while the second tries to take it.
    import threading

    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool")
    session = SessionCore(storage=InMemorySessionStorage())
    overlapped = threading.Event()
    first_inside = threading.Event()
    order: list[str] = []

    def _first() -> None:
        with pool.session_agent(session=session, sink=MagicMock(), logger=logger):
            order.append("first-enter")
            first_inside.set()
            # Without serialization the second thread enters during this wait.
            if overlapped.wait(timeout=0.5):
                order.append("OVERLAP")
            order.append("first-exit")

    def _second() -> None:
        first_inside.wait(timeout=1.0)
        with pool.session_agent(session=session, sink=MagicMock(), logger=logger):
            order.append("second-enter")
            overlapped.set()

    threads = [threading.Thread(target=_first), threading.Thread(target=_second)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Assert
    assert "OVERLAP" not in order, order
    assert order.index("first-exit") < order.index("second-enter"), order


def test_different_sessions_still_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serialization is per session — unrelated conversations must not queue."""
    # Arrange
    import threading

    pool = _fake_agent_pool(monkeypatch)
    logger = logging.getLogger("test.pool")
    both_inside = threading.Barrier(2, timeout=5)
    reached = []

    def _hold() -> None:
        session = SessionCore(storage=InMemorySessionStorage())
        with pool.session_agent(session=session, sink=MagicMock(), logger=logger):
            # Times out if the pool serializes across unrelated sessions.
            both_inside.wait()
            reached.append(session.session_id)

    threads = [threading.Thread(target=_hold), threading.Thread(target=_hold)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Assert
    assert len(reached) == 2, reached
