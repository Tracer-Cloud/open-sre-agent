"""Unit tests for the post-route answer finalize seam."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.goal import SessionGoal, attach_session_goal
from core.agent_harness.turns.answer_finalize import finalize_routed_answer
from core.agent_harness.turns.assistant_handoff import AssistantHandoff
from core.agent_harness.turns.evidence_need import (
    EvidenceKind,
    EvidenceNeed,
    EvidenceTier,
    classify_evidence_need,
)


def _metric_l0_need() -> EvidenceNeed:
    return classify_evidence_need(
        handoffs=(
            AssistantHandoff(
                content="count users",
                evidence_kind=EvidenceKind.METRIC_READ,
            ),
        ),
        preferred_sources_for=lambda _k: ("posthog",),
        resolved_integrations={},
    )


def test_finalize_appends_cta_and_requests_stream_finish_when_text_changes() -> None:
    session = SessionCore()
    need = _metric_l0_need()
    assert need.tier is EvidenceTier.L0_DEGRADED

    result = finalize_routed_answer(
        session=session,
        route_intent="gather_and_answer",
        response_text="Outline only.",
        evidence_for_offer=None,
        evidence_need=need,
        handoff_contents=("evidence_kind:metric_read",),
        original_user_text="how many users?",
        confirms_pending=False,
    )

    assert "/integrations" in result.response_text or "posthog" in result.response_text.lower()
    assert result.finish_stream is True


def test_finalize_suppresses_investigate_when_session_goal_active() -> None:
    session = SessionCore()
    attach_session_goal(session, SessionGoal(condition="checklist", max_outer_turns=3))
    need = EvidenceNeed(
        kind=EvidenceKind.INCIDENT,
        preferred_sources=(),
        connected=(),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=False,
    )

    result = finalize_routed_answer(
        session=session,
        route_intent="gather_and_answer",
        response_text="Step 1 done.\n\nWant me to: dig further?",
        evidence_for_offer="evidence",
        evidence_need=need,
        handoff_contents=("session_goal:max_turns=3",),
        original_user_text="keep going",
        confirms_pending=False,
    )

    assert session.pending_investigation_offer is None
    assert "Want me to" not in result.response_text
    # Deferred closer must flush (without Want-me-to) so the TTY drops it.
    assert result.finish_stream is True


def test_finalize_flushes_gather_when_goal_active_even_without_closer() -> None:
    """Footer / deferred paint must flush when the goal owns the turn."""
    session = SessionCore()
    attach_session_goal(session, SessionGoal(condition="triage", max_outer_turns=6))
    need = EvidenceNeed(
        kind=EvidenceKind.INCIDENT,
        preferred_sources=(),
        connected=(),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=False,
    )

    result = finalize_routed_answer(
        session=session,
        route_intent="gather_and_answer",
        response_text="Highest priority: Sentry. First step: nvm use 22.",
        evidence_for_offer="evidence",
        evidence_need=need,
        handoff_contents=(),
        original_user_text="morning triage",
        confirms_pending=False,
    )

    assert session.pending_investigation_offer is None
    assert "Want me to" not in result.response_text
    assert result.finish_stream is True


def test_finalize_strips_want_me_to_when_host_owned_goal_just_achieved() -> None:
    """Evaluate marks ACHIEVED before finalize — still strip the closer."""
    from core.agent_harness.session_goal.goal import SessionGoalStatus

    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="list three steps",
            max_outer_turns=3,
            host_owned=True,
            status=SessionGoalStatus.ACHIEVED,
        ),
    )
    need = EvidenceNeed(
        kind=EvidenceKind.INCIDENT,
        preferred_sources=(),
        connected=(),
        missing=(),
        tier=EvidenceTier.L1,
        required_for_authoritative=False,
    )

    result = finalize_routed_answer(
        session=session,
        route_intent="cli_agent_handled",
        response_text=(
            "1. a\n2. b\n3. c\n\nWant me to: mark this goal achieved now with /goal clear?"
        ),
        evidence_for_offer=None,
        evidence_need=need,
        handoff_contents=(),
        original_user_text="list three steps",
        confirms_pending=False,
    )

    assert session.pending_investigation_offer is None
    assert "Want me to" not in result.response_text
    assert "1. a" in result.response_text
    assert result.finish_stream is False
