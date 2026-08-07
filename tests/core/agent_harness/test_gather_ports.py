"""``GatherPorts`` — one object for how a surface runs the gather phase.

The four settings always vary together per surface: a REPL streams progress and
persists tool calls, a scheduled report does neither and only raises the
iteration budget. Passing them as four loose keywords made the agent constructor
harder to read the more surfaces it served.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness.turns.gather_ports import GatherPorts


class _Session:
    history: list[dict[str, Any]] = []


def test_defaults_gather_with_no_surface_instrumentation() -> None:
    # Arrange / Act: the shape a scheduled digest wants — run the phase, watch
    # nothing, record nothing.
    ports = GatherPorts()

    # Assert
    assert ports.enabled is True
    assert ports.on_progress is None
    assert ports.persist is None
    assert ports.max_iterations is None


def test_disabled_is_expressible_without_a_sentinel() -> None:
    # Arrange / Act
    ports = GatherPorts(enabled=False)

    # Assert: a caller that wants no gather says so, rather than passing a
    # magic iteration count.
    assert ports.enabled is False


def test_is_immutable_so_one_surfaces_ports_cannot_leak_into_another() -> None:
    # Arrange
    ports = GatherPorts()

    # Act / Assert: agents are pooled per session on the gateway; a mutable
    # ports object would let one turn retarget another's progress stream.
    with pytest.raises(AttributeError):
        ports.enabled = False  # type: ignore[misc]


class TestAgentForwardsThePorts:
    def test_progress_and_persist_reach_the_gather_phase(self, monkeypatch) -> None:
        # Arrange: a REPL supplies both so the user sees live tool lines and the
        # calls land in session history.
        from core.agent_harness.turns import headless_dispatch

        seen: dict[str, Any] = {}

        def _fake_gather(_message: str, _session: Any, **kwargs: Any) -> str | None:
            seen.update(kwargs)
            return "evidence"

        monkeypatch.setattr(headless_dispatch, "gather_tool_evidence", _fake_gather)

        def _on_progress(_kind: str, _data: dict[str, Any]) -> None:
            return None

        def _persist(_executed: list[tuple[Any, Any]]) -> None:
            return None

        agent = headless_dispatch.HeadlessAgent(
            tools=headless_dispatch.NullToolProvider(),
            session=headless_dispatch.InMemorySessionStore(),
            gather=GatherPorts(on_progress=_on_progress, persist=_persist, max_iterations=9),
        )

        # Act
        result = agent._gather("why is it slow?")  # noqa: SLF001

        # Assert
        assert result == "evidence"
        assert seen["on_progress"] is _on_progress
        assert seen["persist"] is _persist
        assert seen["max_iterations"] == 9

    def test_disabled_ports_skip_the_phase_entirely(self, monkeypatch) -> None:
        # Arrange
        from core.agent_harness.turns import headless_dispatch

        def _never(*_args: Any, **_kwargs: Any) -> str | None:
            raise AssertionError("gather ran while disabled")

        monkeypatch.setattr(headless_dispatch, "gather_tool_evidence", _never)

        agent = headless_dispatch.HeadlessAgent(
            tools=headless_dispatch.NullToolProvider(),
            session=headless_dispatch.InMemorySessionStore(),
            gather=GatherPorts(enabled=False),
        )

        # Act / Assert
        assert agent._gather("anything") is None  # noqa: SLF001
