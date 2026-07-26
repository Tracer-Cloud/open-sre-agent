"""Gather-pass system prompt grounding."""

from __future__ import annotations

from typing import Any

from core.agent_harness.prompts.gather import (
    build_gather_system_prompt,
    build_gather_system_prompt_from_turn_snapshot,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


class _SessionView:
    configured_integrations: tuple[str, ...] = ("datadog", "sentry")
    last_state: dict[str, Any] | None = None


class _SnapshotSession:
    cli_agent_messages: list[tuple[str, str]] = []
    configured_integrations = ("sentry",)
    configured_integrations_known = True
    last_state: dict[str, Any] | None = {"root_cause": "disk full on orders-api"}
    last_synthetic_observation_path = None
    reasoning_effort = None


def test_gather_prompt_without_prior_state_omits_prior_block() -> None:
    prompt = build_gather_system_prompt(_SessionView())  # type: ignore[arg-type]

    assert "Configured integrations in this session: datadog, sentry." in prompt
    assert "Prior investigation in this session" not in prompt


def test_gather_prompt_with_prior_state_instructs_no_tools_for_retrospectives() -> None:
    session = _SessionView()
    session.last_state = {"root_cause": "disk full on orders-api", "alert_name": "disk"}
    prompt = build_gather_system_prompt(session)  # type: ignore[arg-type]

    assert "--- Prior investigation in this session ---" in prompt
    assert "Root cause: disk full on orders-api" in prompt
    assert "call NO tools" in prompt


def test_gather_prompt_from_turn_snapshot_includes_last_state() -> None:
    snapshot = TurnSnapshot.from_session("what happened?", _SnapshotSession())
    prompt = build_gather_system_prompt_from_turn_snapshot(snapshot)

    assert "Root cause: disk full on orders-api" in prompt
    assert "call NO tools" in prompt
