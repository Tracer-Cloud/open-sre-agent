"""Unit tests for SessionGoal evaluate decision conditions."""

from __future__ import annotations

import pytest

from core.agent_harness.session_goal.evaluate import (
    _host_owned_achieved_claim_lacks_tool_evidence,
    _host_owned_goal_has_tool_evidence_and_answer_reply,
    _host_owned_goal_has_unverified_cohort_reply,
    _reply_is_nonempty_and_not_progress_text,
    _short_checklist_has_achieved_claim_and_tool_evidence,
    _short_checklist_has_no_prior_progress_and_tool_answer,
)
from core.agent_harness.session_goal.goal import SessionGoal


def _goal(
    *,
    condition: str = "count windows users",
    host_owned: bool = True,
    checklist: tuple[str, ...] = (),
    completed: frozenset[int] = frozenset(),
) -> SessionGoal:
    return SessionGoal(
        condition=condition,
        max_outer_turns=4,
        host_owned=host_owned,
        checklist=checklist,
        completed=completed,
    )


def test_reply_is_nonempty_and_not_progress_text() -> None:
    assert _reply_is_nonempty_and_not_progress_text("D7 retention is unavailable.") is True
    assert _reply_is_nonempty_and_not_progress_text("   ") is False
    assert _reply_is_nonempty_and_not_progress_text("") is False
    progress_text = "◎ /goal active\n  reason: waiting for host signal"
    assert _reply_is_nonempty_and_not_progress_text(progress_text) is False


def test_host_owned_goal_has_unverified_cohort_reply_requires_all_parts() -> None:
    cohort = _goal(
        condition="D7 retention for users who signed up on Windows",
        host_owned=True,
    )
    refuse = "signup event unverified — cannot provide a retention percentage."
    assert _host_owned_goal_has_unverified_cohort_reply(cohort, refuse) is True

    assert (
        _host_owned_goal_has_unverified_cohort_reply(
            _goal(condition=cohort.condition, host_owned=False),
            refuse,
        )
        is False
    )
    assert (
        _host_owned_goal_has_unverified_cohort_reply(
            _goal(condition="log retention 30 days", host_owned=True),
            refuse,
        )
        is False
    )
    assert _host_owned_goal_has_unverified_cohort_reply(cohort, "D7 retention is 41%.") is False
    assert (
        _host_owned_goal_has_unverified_cohort_reply(
            cohort,
            "◎ /goal active\n  reason: waiting for host signal",
        )
        is False
    )


def test_host_owned_goal_has_tool_evidence_and_answer_reply() -> None:
    goal = _goal(host_owned=True)
    assert (
        _host_owned_goal_has_tool_evidence_and_answer_reply(
            goal,
            "279 Windows users.",
            has_evidence=True,
        )
        is True
    )
    assert (
        _host_owned_goal_has_tool_evidence_and_answer_reply(
            goal,
            "279 Windows users.",
            has_evidence=False,
        )
        is False
    )
    assert (
        _host_owned_goal_has_tool_evidence_and_answer_reply(
            _goal(host_owned=False),
            "279 Windows users.",
            has_evidence=True,
        )
        is False
    )
    assert (
        _host_owned_goal_has_tool_evidence_and_answer_reply(goal, "   ", has_evidence=True) is False
    )


def test_host_owned_achieved_claim_lacks_tool_evidence() -> None:
    assert (
        _host_owned_achieved_claim_lacks_tool_evidence(_goal(host_owned=True), has_evidence=False)
        is True
    )
    assert (
        _host_owned_achieved_claim_lacks_tool_evidence(_goal(host_owned=True), has_evidence=True)
        is False
    )
    assert (
        _host_owned_achieved_claim_lacks_tool_evidence(
            _goal(host_owned=False),
            has_evidence=False,
        )
        is False
    )


@pytest.mark.parametrize(
    ("checklist_len", "claimed", "has_evidence", "expected"),
    [
        (2, True, True, True),
        (2, True, False, False),
        (2, False, True, False),
        (3, True, True, False),
    ],
)
def test_short_checklist_has_achieved_claim_and_tool_evidence(
    checklist_len: int,
    claimed: bool,
    has_evidence: bool,
    expected: bool,
) -> None:
    goal = _goal(checklist=tuple(f"step {i}" for i in range(checklist_len)))
    assert (
        _short_checklist_has_achieved_claim_and_tool_evidence(
            goal,
            claimed=claimed,
            has_evidence=has_evidence,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("checklist_len", "has_evidence", "text", "completed", "expected"),
    [
        (2, True, "done", frozenset(), True),
        (2, True, "done", frozenset({0}), False),
        (2, False, "done", frozenset(), False),
        (2, True, "   ", frozenset(), False),
        (3, True, "done", frozenset(), False),
    ],
)
def test_short_checklist_has_no_prior_progress_and_tool_answer(
    checklist_len: int,
    has_evidence: bool,
    text: str,
    completed: frozenset[int],
    expected: bool,
) -> None:
    goal = _goal(
        checklist=tuple(f"step {i}" for i in range(checklist_len)),
        completed=completed,
    )
    assert (
        _short_checklist_has_no_prior_progress_and_tool_answer(
            goal,
            has_evidence=has_evidence,
            text=text,
            completed_before=completed,
        )
        is expected
    )
