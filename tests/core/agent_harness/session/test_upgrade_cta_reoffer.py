"""L0 UpgradeCTA re-offers after the user moves on (pending offer cleared)."""

from __future__ import annotations

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.session.pending_offer import (
    PendingIntegrationSetupOffer,
    first_pending_offer,
)
from core.agent_harness.turns.answer_finalize import append_upgrade_cta
from core.agent_harness.turns.evidence_need import classify_evidence_need
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from infrastructure.harness_ports import preferred_evidence_sources_for
from surfaces.interactive_shell.session import Session


def _metric_execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=True,
        handled=False,
        handoff_contents=("evidence_kind:metric_read", "Look up Windows users."),
    )


def _run_metric_ask(session: Session, text: str = "how many windows users") -> str:
    result = run_turn(
        text,
        session,
        execute_actions=_metric_execute,
        gather=lambda *_a, **_k: "should-not-run",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Draft outline."})(),
        accounting=DefaultTurnAccounting(session, text),
    )
    return result.assistant_response_text or ""


def test_cta_dedupes_while_pending_offer_is_armed() -> None:
    """Same-turn / still-armed offer must not append a second CTA block."""
    session = Session()
    session.resolved_integrations_cache = {}
    need = classify_evidence_need(
        handoff_contents=("evidence_kind:metric_read",),
        resolved_integrations={},
        preferred_sources_for=preferred_evidence_sources_for,
    )
    first = append_upgrade_cta(session, "Draft outline.", need)
    assert "/integrations setup posthog_mcp" in first
    assert isinstance(first_pending_offer(session), PendingIntegrationSetupOffer)

    second = append_upgrade_cta(session, first, need)
    assert second == first
    assert second.count("/integrations setup posthog_mcp") == 1


def test_cta_reoffers_after_user_moves_on_without_accepting() -> None:
    session = Session()
    session.resolved_integrations_cache = {}
    first = _run_metric_ask(session)
    assert "/integrations setup posthog_mcp" in first
    assert isinstance(first_pending_offer(session), PendingIntegrationSetupOffer)

    # Non-confirm turn clears the stale pending offer.
    run_turn(
        "tell me about something else entirely",
        session,
        execute_actions=lambda *_a, **_k: ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=("chat:other",),
        ),
        gather=lambda *_a, **_k: "",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Sure."})(),
        accounting=DefaultTurnAccounting(session, "other"),
    )
    assert first_pending_offer(session) is None

    again = _run_metric_ask(session, "how many windows users last 7 days")
    assert "/integrations setup posthog_mcp" in again
    assert isinstance(first_pending_offer(session), PendingIntegrationSetupOffer)
