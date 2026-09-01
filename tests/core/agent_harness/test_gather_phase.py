"""``GatherPhase`` — one object for how a surface runs the gather phase.

The four settings always vary together per surface: a REPL streams progress and
persists tool calls, a scheduled report does neither and only raises the
iteration budget. Passing them as four loose keywords made the agent constructor
harder to read the more surfaces it served.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.agent_harness.ports import TurnBinding
from core.agent_harness.turns.gather_phase import GatherPhase
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.tool.execution import ToolExecutionHooks


class _Session:
    history: list[dict[str, Any]] = []


def test_defaults_gather_with_no_surface_instrumentation() -> None:
    # Arrange / Act: the shape a scheduled digest wants — run the phase, watch
    # nothing, record nothing.
    gather = GatherPhase()

    # Assert
    assert gather.enabled is True
    assert gather.on_progress is None
    assert gather.persist is None
    assert gather.max_iterations is None


def test_disabled_is_expressible_without_a_sentinel() -> None:
    # Arrange / Act
    gather = GatherPhase(enabled=False)

    # Assert: a caller that wants no gather says so, rather than passing a
    # magic iteration count.
    assert gather.enabled is False


def test_is_immutable_so_one_surfaces_gather_cannot_leak_into_another() -> None:
    # Arrange
    gather = GatherPhase()

    # Act / Assert: agents are pooled per session on the gateway; a mutable
    # GatherPhase would let one turn retarget another's progress stream.
    with pytest.raises(AttributeError):
        gather.enabled = False  # type: ignore[misc]


class TestAgentForwardsGatherPhase:
    def test_progress_and_persist_reach_the_gather_phase(self, monkeypatch) -> None:
        # Arrange: a REPL supplies both so the user sees live tool lines and the
        # calls land in session history.
        from core.agent_harness.turns import headless_adapters, headless_agent

        seen: dict[str, Any] = {}

        def _fake_gather(_message: str, _session: Any, **kwargs: Any) -> str | None:
            seen.update(kwargs)
            return "evidence"

        monkeypatch.setattr(headless_agent, "gather_tool_evidence", _fake_gather)

        def _on_progress(_kind: str, _data: dict[str, Any]) -> None:
            return None

        def _persist(_executed: list[tuple[str, Mapping[str, object], Any]]) -> None:
            return None

        agent = InMemoryHeadlessBuild(session=headless_adapters.InMemorySessionState()).agent(
            tools=headless_adapters.NullToolProvider(),
            gather=GatherPhase(on_progress=_on_progress, persist=_persist, max_iterations=9),
        )

        # Act
        result = agent._gather("why is it slow?")  # noqa: SLF001

        # Assert
        assert result == "evidence"
        assert seen["on_progress"] is _on_progress
        assert seen["persist"] is _persist
        assert seen["max_iterations"] == 9

    def test_turn_tool_hooks_reach_the_gather_phase(self, monkeypatch) -> None:
        from core.agent_harness.turns import headless_adapters, headless_agent

        seen: dict[str, Any] = {}

        def _fake_gather(_message: str, _session: Any, **kwargs: Any) -> str | None:
            seen.update(kwargs)
            return "evidence"

        monkeypatch.setattr(headless_agent, "gather_tool_evidence", _fake_gather)
        hooks = ToolExecutionHooks()
        agent = InMemoryHeadlessBuild(session=headless_adapters.InMemorySessionState()).agent(
            tools=headless_adapters.NullToolProvider(),
            gather=GatherPhase(),
        )
        agent.bind_turn(TurnBinding(tool_hooks=hooks))

        assert agent._gather("why is it slow?") == "evidence"  # noqa: SLF001
        assert seen["tool_hooks"] is hooks

    def test_disabled_gather_skips_the_phase_entirely(self, monkeypatch) -> None:
        # Arrange
        from core.agent_harness.turns import headless_adapters, headless_agent

        def _never(*_args: Any, **_kwargs: Any) -> str | None:
            raise AssertionError("gather ran while disabled")

        monkeypatch.setattr(headless_agent, "gather_tool_evidence", _never)

        agent = InMemoryHeadlessBuild(session=headless_adapters.InMemorySessionState()).agent(
            tools=headless_adapters.NullToolProvider(),
            gather=GatherPhase(enabled=False),
        )

        # Act / Assert
        assert agent._gather("anything") is None  # noqa: SLF001
