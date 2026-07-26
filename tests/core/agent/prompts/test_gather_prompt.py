"""Gather-pass system prompt grounding."""

from __future__ import annotations

import time
from typing import Any

from core.agent_harness.prompts import build_cli_agent_prompt_from_provider
from core.agent_harness.prompts.gather import (
    build_gather_system_prompt,
    build_gather_system_prompt_from_turn_snapshot,
)
from core.agent_harness.prompts.prior_investigation import (
    PRIOR_INVESTIGATION_RECALL_SECONDS,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


class _StubPrompts:
    """Deterministic grounding text so only prior-investigation recall varies."""

    def surface(self) -> str:
        return "interactive_shell"

    def cli_reference(self) -> str:
        return "=== opensre --help ===\n"

    def agents_md(self) -> str:
        return ""

    def docs(self, query: str) -> str:  # noqa: ARG002 - stub
        return ""

    def investigation_flow(self) -> str:
        return ""

    def environment_block(self) -> str:
        return ""

    def suggested_synthetic_prompt(self) -> str:
        return ""

    def log_diagnostics(self, reason: str) -> None:  # noqa: ARG002 - stub
        return None


def _investigation(**overrides: Any) -> dict[str, Any]:
    """A just-finished investigation state, unless overridden."""
    state: dict[str, Any] = {
        "alert_name": "disk",
        "root_cause": "disk full on orders-api",
        "investigation_started_at": time.monotonic(),
    }
    state.update(overrides)
    return state


class _SessionView:
    configured_integrations: tuple[str, ...] = ("datadog", "sentry")
    last_state: dict[str, Any] | None = None


class _SnapshotSession:
    cli_agent_messages: list[tuple[str, str]] = []
    configured_integrations = ("sentry",)
    configured_integrations_known = True
    last_state: dict[str, Any] | None = None
    last_synthetic_observation_path = None
    reasoning_effort = None


def test_gather_prompt_without_prior_state_omits_prior_block() -> None:
    # Arrange / Act
    prompt = build_gather_system_prompt(_SessionView())  # type: ignore[arg-type]

    # Assert
    assert "Configured integrations in this session: datadog, sentry." in prompt
    assert "Prior investigation in this session" not in prompt


def test_gather_prompt_with_recent_state_instructs_no_tools_for_retrospectives() -> None:
    # Arrange
    session = _SessionView()
    session.last_state = _investigation()

    # Act
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    # Assert
    assert "--- Prior investigation in this session ---" in prompt
    assert "Root cause: disk full on orders-api" in prompt
    assert "call NO tools" in prompt


def test_gather_prompt_drops_prior_block_once_investigation_is_stale() -> None:
    """An old investigation must not suppress tools for a question about something new."""
    # Arrange: started just past the recall window.
    session = _SessionView()
    session.last_state = _investigation(
        investigation_started_at=time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
        root_cause="stale-marker-must-not-appear",
    )

    # Act
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    # Assert: the turn falls back to gathering, and the stale finding never leaks.
    assert "Prior investigation in this session" not in prompt
    assert "call NO tools" not in prompt
    assert "stale-marker-must-not-appear" not in prompt


def test_gather_prompt_drops_prior_block_when_state_has_no_timestamp() -> None:
    """Undatable state is treated as stale, not as fresh."""
    # Arrange
    session = _SessionView()
    session.last_state = {"root_cause": "undatable-marker-must-not-appear"}

    # Act
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    # Assert
    assert "Prior investigation in this session" not in prompt
    assert "undatable-marker-must-not-appear" not in prompt


def test_gather_prompt_from_turn_snapshot_includes_recent_last_state() -> None:
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation()

    # Act
    snapshot = TurnSnapshot.from_session("what happened?", session)
    prompt = build_gather_system_prompt_from_turn_snapshot(snapshot)

    # Assert
    assert "Root cause: disk full on orders-api" in prompt
    assert "call NO tools" in prompt


def test_answer_prompt_uses_the_same_recall_window_as_gather() -> None:
    """Both prompts must agree: a stale RCA cannot ground the answer either.

    Otherwise the turn gathers live evidence while the answer prompt still leads
    with the old root cause.
    """
    # Arrange
    stale = _investigation(
        investigation_started_at=time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
        root_cause="stale-marker-must-not-appear",
    )
    session = _SnapshotSession()
    session.last_state = stale
    snapshot = TurnSnapshot.from_session("why did it fail?", session)

    # Act
    gather_prompt = build_gather_system_prompt_from_turn_snapshot(snapshot)
    answer_prompt = build_cli_agent_prompt_from_provider(
        message="why did it fail?",
        prompts=_StubPrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snapshot,
    )

    # Assert: the stale finding reaches neither prompt. (The section *name* still
    # appears in the standing rule that references it; only the data must be gone.)
    assert "stale-marker-must-not-appear" not in gather_prompt
    assert "stale-marker-must-not-appear" not in answer_prompt


def test_answer_prompt_still_grounds_on_a_recent_investigation() -> None:
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation()
    snapshot = TurnSnapshot.from_session("what happened?", session)

    # Act
    answer_prompt = build_cli_agent_prompt_from_provider(
        message="what happened?",
        prompts=_StubPrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snapshot,
    )

    # Assert
    assert "--- Prior investigation in this session ---" in answer_prompt
    assert "disk full on orders-api" in answer_prompt
