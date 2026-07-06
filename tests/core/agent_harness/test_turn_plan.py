"""Unit tests for the turn-wide assembly object ``TurnPlan``."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.session import Session
from core.agent_harness.turns.turn_plan import TurnPlan, build_turn_plan


def _snapshot(text: str = "q", *, resolved: dict | None = None) -> TurnSnapshot:
    snapshot = TurnSnapshot.from_session(text, Session())
    if resolved is not None:
        snapshot = replace(snapshot, resolved_integrations=resolved)
    return snapshot


def test_build_turn_plan_composes_the_snapshot() -> None:
    snapshot = _snapshot("why did it fail?", resolved={"github": {"configured": True}})

    plan = build_turn_plan(snapshot, Session())

    assert isinstance(plan, TurnPlan)
    assert plan.snapshot is snapshot
    assert plan.text == "why did it fail?"


def test_turn_plan_exposes_resolved_integrations_from_snapshot() -> None:
    resolved = {"github": {"configured": True}}
    snapshot = _snapshot(resolved=resolved)

    plan = build_turn_plan(snapshot, Session())

    # The plan is the single source: it reads the snapshot's resolved view, not a copy.
    assert plan.resolved_integrations == resolved
    assert plan.resolved_integrations is plan.snapshot.resolved_integrations


def test_build_turn_plan_resolves_integrations_when_snapshot_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_turn_plan owns the resolve step, running it when the snapshot is empty."""
    resolved = {"datadog": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )

    plan = build_turn_plan(_snapshot(), Session())

    assert plan.resolved_integrations == resolved


def test_build_turn_plan_skips_resolve_when_snapshot_already_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime-request source can pre-fill the snapshot; then no re-resolve runs."""

    def _must_not_run(_session: object) -> dict:
        raise AssertionError("resolve must not run when the snapshot is already populated")

    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations", _must_not_run
    )
    resolved = {"github": {"configured": True}}

    plan = build_turn_plan(_snapshot(resolved=resolved), Session())

    assert plan.resolved_integrations == resolved
