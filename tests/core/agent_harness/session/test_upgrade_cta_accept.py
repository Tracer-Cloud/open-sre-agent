"""Bare yes after L0 UpgradeCTA expands to integrations setup."""

from __future__ import annotations

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import (
    PendingIntegrationSetupOffer,
    first_pending_offer,
)
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from surfaces.interactive_shell.session import Session


def test_l0_cta_arms_integration_setup_offer_and_yes_expands() -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    def _execute(*_a: object, **_k: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=False,
            handoff_contents=(
                "evidence_kind:metric_read",
                "Look up Windows users.",
            ),
        )

    run_turn(
        "how many windows users last 7 days",
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "should-not-run",
        answer=lambda *_a, **_k: type("Run", (), {"response_text": "Draft outline."})(),
        accounting=DefaultTurnAccounting(session, "ask"),
    )

    offer = first_pending_offer(session)
    assert isinstance(offer, PendingIntegrationSetupOffer)
    assert offer.service_id == "posthog_mcp"
    assert "/integrations setup" in offer.to_slash_command()

    expanded = expand_affirmative_follow_up(
        "yes",
        session.cli_agent_messages,
        pending_offer=offer,
    )
    assert expanded.startswith("/integrations setup")
