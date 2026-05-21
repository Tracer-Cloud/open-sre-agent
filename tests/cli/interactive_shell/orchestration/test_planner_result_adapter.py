"""Tests for the planner-result compatibility adapter seam."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.planner_result_adapter import (
    PlannerResult,
    normalize_planner_result,
)


def test_normalize_planner_result_none_is_unavailable() -> None:
    normalized = normalize_planner_result(None)
    assert normalized == PlannerResult(actions=[], has_unhandled_clause=True, unavailable=True)


def test_normalize_planner_result_legacy_tuple_shape() -> None:
    action = PlannedAction(kind="slash", content="/health", position=0, source="llm")
    normalized = normalize_planner_result(([action], False))
    assert normalized.unavailable is False
    assert normalized.has_unhandled_clause is False
    assert normalized.actions == [action]


def test_normalize_planner_result_rejects_invalid_shape() -> None:
    with pytest.raises(TypeError):
        normalize_planner_result(("bad", False))
