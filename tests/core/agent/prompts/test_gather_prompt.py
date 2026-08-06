"""Gather-pass system prompt grounding."""

from __future__ import annotations

import time
from typing import Any

from core.agent_harness.prompts import (
    build_action_system_prompt,
    build_cli_agent_prompt_from_provider,
)
from core.agent_harness.prompts.assistant import build_handoff_guidance_block
from core.agent_harness.prompts.gather import (
    build_gather_system_prompt,
    build_gather_system_prompt_from_turn_snapshot,
)
from core.agent_harness.prompts.memory.prior_investigation import (
    PRIOR_INVESTIGATION_RECALL_SECONDS,
    STALE_PRIOR_INVESTIGATION_NOTE,
)
from core.agent_harness.turns.orchestrator import _is_prior_investigation_follow_up_handoff
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from integrations.github.gather_prompt import github_gather_prompt_fragment


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

    def runtime_facts(self) -> dict[str, object]:

        from config.runtime_metadata import capture_runtime_facts

        return capture_runtime_facts()

    def environment_block(self, runtime=None) -> str:  # noqa: ANN001, ARG002
        return ""

    def long_term_memory(self) -> str:
        return ""

    def setup_state(self) -> str:
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


def test_gather_prompt_keeps_stale_findings_but_stops_suppressing_tools() -> None:
    """Age lifts the no-tools licence; it must not discard the findings themselves."""
    # Arrange: started just past the recall window.
    session = _SessionView()
    session.last_state = _investigation(
        investigation_started_at=time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
        root_cause="aging-root-cause",
    )

    # Act
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    # Assert: still available as background, but the turn now gathers fresh data.
    assert "Root cause: aging-root-cause" in prompt
    assert "call NO tools" not in prompt
    assert "DO gather fresh data" in prompt


def test_gather_prompt_treats_undatable_state_as_stale() -> None:
    """State we cannot date keeps its findings but never suppresses tools."""
    # Arrange
    session = _SessionView()
    session.last_state = {"root_cause": "undatable-root-cause"}

    # Act
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    # Assert
    assert "Root cause: undatable-root-cause" in prompt
    assert "call NO tools" not in prompt


def test_gather_prompt_from_turn_snapshot_includes_recent_last_state() -> None:
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation()

    # Act
    snapshot = TurnSnapshot.from_session("what happened?", session, surface="interactive_shell")
    prompt = build_gather_system_prompt_from_turn_snapshot(snapshot)

    # Assert
    assert "Root cause: disk full on orders-api" in prompt
    assert "call NO tools" in prompt


def test_github_gather_prompt_routes_star_history_to_dedicated_tool() -> None:
    prompt = github_gather_prompt_fragment()

    assert "get_github_star_history" in prompt
    assert "day-by-day stars" in prompt
    assert "execute_python_code" in prompt


def test_answer_prompt_keeps_an_old_investigation_and_labels_it() -> None:
    """A retrospective question can come at any time; the RCA must survive the window.

    Dropping it would make OpenSRE claim it lacks incident details it still holds.
    """
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation(
        investigation_started_at=time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
        root_cause="aging-root-cause",
    )
    snapshot = TurnSnapshot.from_session("what happened?", session, surface="interactive_shell")

    # Act
    answer_prompt = build_cli_agent_prompt_from_provider(
        message="what happened?",
        prompts=_StubPrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snapshot,
    )

    # Assert: still answerable, but flagged so fresh evidence wins a conflict.
    assert "aging-root-cause" in answer_prompt
    assert STALE_PRIOR_INVESTIGATION_NOTE in answer_prompt


def test_answer_prompt_does_not_label_a_recent_investigation_as_stale() -> None:
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation()
    snapshot = TurnSnapshot.from_session("what happened?", session, surface="interactive_shell")

    # Act
    answer_prompt = build_cli_agent_prompt_from_provider(
        message="what happened?",
        prompts=_StubPrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snapshot,
    )

    # Assert
    assert "disk full on orders-api" in answer_prompt
    assert STALE_PRIOR_INVESTIGATION_NOTE not in answer_prompt


def test_answer_prompt_still_grounds_on_a_recent_investigation() -> None:
    # Arrange
    session = _SnapshotSession()
    session.last_state = _investigation()
    snapshot = TurnSnapshot.from_session("what happened?", session, surface="interactive_shell")

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


def test_action_prompt_teaches_the_follow_up_handoff_tag() -> None:
    """The gather skip is unreachable unless the planner is told to emit the tag.

    ``_is_prior_investigation_follow_up_handoff`` matches ``follow_up:``; nothing
    produced that prefix until the action prompt taught it, so the skip was dead.
    """
    # Arrange / Act
    prompt = build_action_system_prompt(
        TurnSnapshot.from_session("what happened?", _SnapshotSession(), surface="interactive_shell")
    )

    # Assert: the emitted value must satisfy the orchestrator's prefix check.
    assert 'assistant_handoff(content="follow_up:prior_investigation")' in prompt
    assert _is_prior_investigation_follow_up_handoff(("follow_up:prior_investigation",))


def test_assistant_has_guidance_for_the_follow_up_tag() -> None:
    # Act
    guidance = build_handoff_guidance_block(("follow_up:prior_investigation",))

    # Assert
    assert "lead with the root cause" in guidance


def test_explicit_follow_up_answers_from_an_old_investigation_unlabelled() -> None:
    """The planner's tag outranks the clock.

    It classified *which* incident the user means, so a stale label would push the
    model toward current conditions instead of what happened in that incident.
    """
    # Arrange: the investigation ran well outside the recall window.
    session = _SnapshotSession()
    session.last_state = _investigation(
        investigation_started_at=time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
        root_cause="the-incident-they-asked-about",
    )
    snapshot = TurnSnapshot.from_session("what happened?", session, surface="interactive_shell")

    # Act
    answer_prompt = build_cli_agent_prompt_from_provider(
        message="what happened?",
        prompts=_StubPrompts(),
        tool_observation=None,
        tool_observation_on_screen=True,
        turn_snapshot=snapshot,
        handoff_contents=("follow_up:prior_investigation",),
    )

    # Assert: the findings lead the answer, not flagged as background.
    assert "the-incident-they-asked-about" in answer_prompt
    assert STALE_PRIOR_INVESTIGATION_NOTE not in answer_prompt


def test_gather_prompt_is_assembled_in_the_same_layers_as_the_action_prompt() -> None:
    """Three layers has to mean every prompt, not just the action one.

    A builder that returns a bare string cannot be cached, trimmed or reasoned
    about by tier, so it silently opts out of the assembly contract.
    """
    # Arrange
    from core.agent_harness.prompts import PromptTier, build_gather_system_prompt_envelope

    session = _SessionView()

    # Act
    envelope = build_gather_system_prompt_envelope(session)
    tiers = [block.tier for block in envelope.blocks]

    # Assert
    assert envelope.render() == build_gather_system_prompt(session)
    assert PromptTier.STABLE in tiers
    assert tiers == sorted(tiers, key=lambda tier: list(PromptTier).index(tier))
