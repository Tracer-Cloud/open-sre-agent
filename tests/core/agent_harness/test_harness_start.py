"""The two-line entry point: start a harness, dispatch a message.

``main.py`` is the file newcomers read first, so the example in it has to be the
API we actually want people to use — not a transcription of the wiring. Before
this, a headless caller assembled config, startup, a console, a logger, an output
sink and a factory call, then attached the result. All of that is defaultable.
"""

from __future__ import annotations

from typing import Any


def test_start_returns_a_ready_harness() -> None:
    """One call: env resolved, session created, default agent attached."""
    # Arrange / Act
    from core.agent_harness.harness import AgentHarness

    harness = AgentHarness.start()

    # Assert: dispatch works without any further wiring.
    assert harness.agent is not None


def test_start_accepts_a_config_for_callers_that_need_one() -> None:
    """Surfaces that resume a session must still be able to pass config."""
    # Arrange
    from core.agent_harness.harness import AgentHarness, HarnessConfig

    # Act
    harness = AgentHarness.start(HarnessConfig())

    # Assert
    assert harness.agent is not None


def test_started_harness_dispatches_without_extra_wiring(monkeypatch: Any) -> None:
    """The documented two-liner must actually run end to end."""
    # Arrange
    from core.agent_harness.harness import AgentHarness

    harness = AgentHarness.start()
    captured: list[str] = []

    def _fake_dispatch(message: str) -> Any:
        captured.append(message)
        return "ok"

    monkeypatch.setattr(harness.agent, "dispatch", _fake_dispatch)

    # Act
    harness.dispatch_message("why is checkout-api slow?")

    # Assert
    assert captured == ["why is checkout-api slow?"]
