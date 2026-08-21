"""Post-route answer finalize: CTA, investigate offer, stream flush decision.

This module runs *after* a route has produced ``response_text``. It must never
be consulted when *choosing* the route — stream rewrite flags
(``text_changed_after_streaming``) are local to this seam only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.ports import OutputSink, SessionState
from core.agent_harness.session.pending_offer import (
    PendingIntegrationSetupOffer,
    arm_pending_integration_setup_offer,
    arm_pending_investigation_offer,
    finalize_gather_investigation_offer,
)
from core.agent_harness.session.want_me_to import strip_want_me_to_closer
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    session_goal_is_attached,
)
from core.agent_harness.tools.tool_context import capability_not_explicitly_disabled
from core.agent_harness.turns.evidence_need import (
    EvidenceNeed,
    cta_offered_key,
    format_upgrade_cta,
    should_skip_gather,
    should_suppress_investigation_offer,
)
from core.agent_harness.turns.handoff_policy import (
    is_non_investigation_handoff,
    is_prior_investigation_follow_up_handoff,
)
from core.agent_harness.turns.metric_query_floor import apply_unformed_metric_floor
from core.agent_harness.turns.turn_route import RouteIntent
from infrastructure.harness_ports import integration_setup_command


def should_suppress_want_me_to_for_session_goal(session: SessionState) -> bool:
    """True while an session goal is attached, or a host-owned goal just achieved.

    Evaluate often marks ACHIEVED before answer finalize runs. Suppressing only
    on active status lets Want-me-to leak on the achieving turn.
    Attached includes ``paused`` so a user-paused ``/goal`` still owns the UX.
    """
    if session_goal_is_attached(session):
        return True
    goal = getattr(session, "session_goal", None)
    return (
        isinstance(goal, SessionGoal)
        and goal.host_owned
        and goal.status == SessionGoalStatus.ACHIEVED
    )


@dataclass(frozen=True)
class AnswerFinalizeResult:
    """Text after CTA/offer rewrites, plus whether the sink must re-flush."""

    response_text: str
    finish_stream: bool


def finish_streamed_response(output: OutputSink | None, answer: str) -> None:
    """Flush deferred/rewritten gather paint on surfaces that support it."""
    if output is None:
        return
    finish = getattr(output, "finish_streamed_response", None)
    if callable(finish):
        finish(answer)
        return
    finalize = getattr(output, "finalize", None)
    if callable(finalize):
        finalize(answer)


def _offered_upgrade_ctas(session: SessionState) -> set[str]:
    holder: Any = session
    offered = getattr(holder, "offered_upgrade_ctas", None)
    if not isinstance(offered, set):
        offered = set()
        holder.offered_upgrade_ctas = offered
    return offered


def append_upgrade_cta(
    session: SessionState,
    response_text: str,
    evidence_need: EvidenceNeed,
) -> str:
    """Append one UpgradeCTA when L0_degraded and not already awaiting accept.

    Also arms a structured setup offer so bare ``yes`` expands to the connect
    slash without keyword intent routing. Dedupes while a pending setup offer
    for the missing source is still armed; re-offers when the user asks again
    after that offer was cleared (moved on / declined).
    """
    key = cta_offered_key(evidence_need)
    cta = format_upgrade_cta(
        evidence_need,
        setup_command_for=integration_setup_command,
    )
    if key is None or cta is None:
        return response_text
    offered = _offered_upgrade_ctas(session)
    pending = getattr(session, "pending_integration_setup_offer", None)
    pending_matches = (
        isinstance(pending, PendingIntegrationSetupOffer)
        and bool(evidence_need.missing)
        and pending.service_id == evidence_need.missing[0]
    )
    if key in offered and pending_matches:
        return response_text
    if key in offered and not pending_matches:
        offered.discard(key)
    offered.add(key)
    if evidence_need.missing:
        arm_pending_integration_setup_offer(session, service_id=evidence_need.missing[0])
    base = (response_text or "").rstrip()
    if not base:
        return cta
    if cta in base:
        return base
    return f"{base}\n\n{cta}"


def finalize_routed_answer(
    *,
    session: SessionState,
    route_intent: RouteIntent,
    response_text: str,
    evidence_for_offer: str | None,
    evidence_need: EvidenceNeed,
    handoff_contents: tuple[str, ...],
    original_user_text: str,
    confirms_pending: bool,
) -> AnswerFinalizeResult:
    """Apply CTA + investigate-offer policy; decide whether to re-flush the stream.

    ``finish_stream`` is true when gather paint was deferred / rewritten and the
    host must see the final text. Callers must not use any locals from this
    function when selecting the turn route.
    """
    streamed_text = response_text
    text = response_text
    if should_suppress_investigation_offer(evidence_need) or should_skip_gather(evidence_need):
        text = append_upgrade_cta(session, text, evidence_need)
    # Metric gather that never ran a live query still gets a vendor draft
    # block and one setup slash. Cohort SessionGoals that leave identity
    # unresolved (vendor-registered) still get a draft fence.
    text = apply_unformed_metric_floor(
        text,
        evidence_need,
        observation=evidence_for_offer,
        setup_command_for=integration_setup_command,
        session=session,
    )
    # Bookkeeping only — never feed this into route selection.
    text_changed_after_streaming = text != streamed_text

    suppress_for_goal = should_suppress_want_me_to_for_session_goal(session)
    suppress_investigate = should_suppress_investigation_offer(evidence_need) or suppress_for_goal
    if suppress_investigate:
        stripped = strip_want_me_to_closer(text)
        if stripped != text:
            text = stripped
            text_changed_after_streaming = True
    finish_stream = False
    if (
        not confirms_pending
        and capability_not_explicitly_disabled(session, "investigation")
        and not suppress_investigate
    ):
        if (
            route_intent == "gather_and_answer"
            and not is_prior_investigation_follow_up_handoff(handoff_contents)
            and not is_non_investigation_handoff(handoff_contents)
        ):
            text, _offer = finalize_gather_investigation_offer(
                session,
                user_text=original_user_text,
                assistant_text=text,
                observation=evidence_for_offer,
            )
            finish_stream = True
        else:
            arm_pending_investigation_offer(
                session,
                user_text=original_user_text,
                assistant_text=text,
                observation=evidence_for_offer,
            )
            if route_intent == "gather_and_answer":
                finish_stream = True
    elif route_intent == "gather_and_answer" and (
        text_changed_after_streaming or suppress_for_goal
    ):
        # Flush deferred Want-me-to (or drop it) when a session goal owns
        # continuation — otherwise the TTY keeps a streamed closer.
        finish_stream = True

    return AnswerFinalizeResult(response_text=text, finish_stream=finish_stream)


__all__ = [
    "AnswerFinalizeResult",
    "append_upgrade_cta",
    "finalize_routed_answer",
    "finish_streamed_response",
    "should_suppress_want_me_to_for_session_goal",
]
