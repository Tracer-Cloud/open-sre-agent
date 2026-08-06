"""Structured investigation offers — yes expands without scraping Want-me-to prose."""

from __future__ import annotations

from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import (
    INVESTIGATION_ACCEPT_MARKER,
    DispatchablePendingOffer,
    PendingInvestigationOffer,
    PendingScheduleOffer,
    arm_pending_investigation_offer,
    assistant_offers_full_investigation,
    consume_confirmed_pending_offer,
    first_pending_offer,
    is_pending_offer_confirmation,
    parse_investigation_accept_message,
    synthesize_investigation_alert_text,
)
from core.agent_harness.session.want_me_to import offer_from_assistant_content
from core.agent_harness.turns.action_driver import _literal_investigation_accept_tool_call
from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult


def test_dispatch_message_quotes_alert_text() -> None:
    offer = PendingInvestigationOffer(alert_text="why is the database slow?")
    msg = offer.to_dispatch_message()
    assert msg.startswith(INVESTIGATION_ACCEPT_MARKER)
    assert parse_investigation_accept_message(msg) == "why is the database slow?"


def test_dispatch_message_keeps_spaces_and_quotes() -> None:
    offer = PendingInvestigationOffer(alert_text="don't drop the table; why OOM?")
    assert parse_investigation_accept_message(offer.to_dispatch_message()) == (
        "don't drop the table; why OOM?"
    )


def test_yes_uses_pending_investigation_not_prose() -> None:
    pending = PendingInvestigationOffer(
        alert_text="why is the database slow?\n\nEvidence gathered:\np95 latency up"
    )
    history = [
        (
            "assistant",
            "Latency is elevated.\n\n**Want me to:** schedule a morning report?",
        ),
    ]
    expanded = expand_affirmative_follow_up(
        "yes",
        history,
        pending_investigation=pending,
    )
    assert expanded.startswith(INVESTIGATION_ACCEPT_MARKER)
    assert "schedule" not in expanded
    assert parse_investigation_accept_message(expanded) == pending.alert_text


def test_arm_clears_competing_schedule_offer() -> None:
    """Only one pending affirmative — investigate arm drops schedule."""
    session = InMemorySessionStore()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
    )
    arm_pending_investigation_offer(
        session,
        user_text="why slow?",
        assistant_text="Hot.\n\n**Want me to:** run a full investigation.",
        observation="p95 up",
    )
    assert session.pending_investigation_offer is not None
    assert session.pending_schedule_offer is None
    # With only investigation pending, yes expands to investigation — not /cron.
    expanded = expand_affirmative_follow_up(
        "yes",
        None,
        pending_offer=first_pending_offer(session),
    )
    assert expanded.startswith(INVESTIGATION_ACCEPT_MARKER)


def test_assistant_offers_full_investigation_detects_canonical_closer() -> None:
    assert assistant_offers_full_investigation(
        "DB looks hot.\n\n**Want me to:** run a full investigation?"
    )
    assert not assistant_offers_full_investigation(
        "DB looks hot.\n\n**Want me to:** schedule this as a recurring daily_summary?"
    )
    # Vendor "investigate X" must not arm a full-investigation pending.
    assert not assistant_offers_full_investigation(
        "Here is the roster.\n\n**Want me to:** investigate those names in Slack?"
    )
    assert not assistant_offers_full_investigation("No closer here.")


def test_want_me_to_body_is_canonical_closer() -> None:
    offer = PendingInvestigationOffer(alert_text="why slow?")
    assert offer.want_me_to_body() == "run a full investigation"
    assert assistant_offers_full_investigation(
        f"Done.\n\n**Want me to:** {offer.want_me_to_body()}?"
    )


def test_arm_pending_investigation_offer_sets_session() -> None:
    session = InMemorySessionStore()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
    )
    offer = arm_pending_investigation_offer(
        session,
        user_text="why is the database slow?",
        assistant_text=(
            "Query latency is up in Datadog.\n\n**Want me to:** run a full investigation."
        ),
        observation="p95=2.1s on checkout-db",
    )
    assert offer is not None
    assert session.pending_investigation_offer is not None
    assert "why is the database slow?" in session.pending_investigation_offer.alert_text
    assert "p95=2.1s" in session.pending_investigation_offer.alert_text
    assert session.pending_schedule_offer is None


def test_literal_investigation_accept_emits_investigation_start() -> None:
    class _Tool:
        name = "investigation_start"

    msg = PendingInvestigationOffer(alert_text="orders-api OOM").to_dispatch_message()
    call = _literal_investigation_accept_tool_call(msg, [_Tool()])
    assert call is not None
    assert call.name == "investigation_start"
    assert call.input["alert_text"] == "orders-api OOM"


def test_run_turn_arms_then_yes_dispatches_investigation() -> None:
    """Multi-turn accept path: diagnostic answer arms offer; yes expands+consumes."""
    session = InMemorySessionStore()
    seen: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        seen.append(text)
        # Diagnostic turn: hand off so gather+answer runs.
        if not text.startswith(INVESTIGATION_ACCEPT_MARKER):
            return ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
                response_text="",
                handoff_contents=("diagnostic:database_slow",),
            )
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="investigation started",
            investigation_dispatched=True,
        )

    class _Run:
        response_text = (
            "Datadog shows elevated DB latency.\n\n**Want me to:** run a full investigation."
        )

    def answer(text: str, request: object = None) -> _Run:
        _ = text, request
        return _Run()

    def gather(text: str, *, turn_plan: object = None) -> str:
        _ = text, turn_plan
        return "p95 latency 2.1s on checkout-db"

    run_turn(
        "why is the database slow?",
        session,
        execute_actions=execute_actions,
        answer=answer,
        gather=gather,
        accounting=NoopTurnAccounting(),
    )

    assert session.pending_investigation_offer is not None
    assert "why is the database slow?" in session.pending_investigation_offer.alert_text
    assert "p95 latency" in session.pending_investigation_offer.alert_text

    seen.clear()
    run_turn(
        "yes",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: None,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
    )

    assert len(seen) == 1
    assert seen[0].startswith(INVESTIGATION_ACCEPT_MARKER)
    assert parse_investigation_accept_message(seen[0]) is not None
    assert "why is the database slow?" in (parse_investigation_accept_message(seen[0]) or "")
    assert session.pending_investigation_offer is None


def test_failed_investigation_keeps_pending_offer() -> None:
    session = InMemorySessionStore()
    session.pending_investigation_offer = PendingInvestigationOffer(
        alert_text="why is checkout 502?"
    )

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        assert text.startswith(INVESTIGATION_ACCEPT_MARKER)
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
            response_text="failed",
        )

    run_turn(
        "yes",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: None,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
    )
    assert session.pending_investigation_offer is not None


def test_synthesize_alert_includes_evidence() -> None:
    text = synthesize_investigation_alert_text("why slow?", "metric=high")
    assert text.startswith("why slow?")
    assert "metric=high" in text


def test_dispatchable_protocol_and_consume_helpers() -> None:
    schedule = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
    )
    investigation = PendingInvestigationOffer(alert_text="why slow?")
    assert isinstance(schedule, DispatchablePendingOffer)
    assert isinstance(investigation, DispatchablePendingOffer)
    assert schedule.to_dispatch_message() == schedule.to_slash_command()

    session = InMemorySessionStore()
    session.pending_schedule_offer = schedule
    session.pending_investigation_offer = investigation
    # Schedule attr wins priority when both are set.
    assert first_pending_offer(session) is schedule
    expanded = schedule.to_dispatch_message()
    assert is_pending_offer_confirmation(session, expanded)
    assert consume_confirmed_pending_offer(session, expanded) is True
    assert session.pending_schedule_offer is None
    assert session.pending_investigation_offer is investigation


def test_want_me_to_extractor_lives_outside_conversation() -> None:
    assert (
        offer_from_assistant_content("Done.\n\n**Want me to:** run a full investigation?")
        == "run a full investigation"
    )
