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
    ensure_canonical_investigation_closer,
    finalize_gather_investigation_offer,
    first_pending_offer,
    is_pending_offer_confirmation,
    is_schedule_only_want_me_to,
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
    # Dogfood: model said "kick off a full investigation as soon as you paste…"
    assert assistant_offers_full_investigation(
        "Need more context.\n\n**Want me to:** kick off a full investigation as "
        "soon as you paste the alert text / JSON, or name the DB."
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


def test_ensure_canonical_closer_rewrites_dogfood_kick_off_variant() -> None:
    """Dogfood: model offered 'kick off a full investigation as soon as you paste…'."""
    raw = (
        "I don't have enough to pin a root cause yet.\n\n"
        "**Want me to:** kick off a full investigation as soon as you paste "
        "the alert text / JSON, or name the DB + approx time window?"
    )
    fixed = ensure_canonical_investigation_closer(raw)
    assert fixed.endswith("**Want me to:** run a full investigation")
    assert "paste" not in fixed.split("**Want me to:**", 1)[1].lower()
    assert assistant_offers_full_investigation(fixed)


def test_ensure_canonical_closer_rewrites_dual_integrations_menu() -> None:
    """Dogfood: gather closed with dual Want-me-to including /integrations setup."""
    raw = (
        "Checkout looks unhealthy; Grafana probe failed.\n\n"
        "**Want me to:**\n"
        "1. run a full investigation once you paste the alert / JSON "
        "(or confirm the service + time window), or\n"
        "2. walk you through `/integrations setup grafana` to get a live "
        "Grafana connection first?"
    )
    fixed = ensure_canonical_investigation_closer(raw)
    assert fixed.count("**Want me to:**") == 1
    assert fixed.rstrip().endswith("**Want me to:** run a full investigation")
    assert "/integrations" not in fixed
    assert not is_schedule_only_want_me_to(fixed)


def test_ensure_canonical_closer_leaves_schedule_only_alone() -> None:
    schedule = (
        "Saved.\n\n**Want me to:** schedule this as a recurring daily_summary?"
    )
    assert ensure_canonical_investigation_closer(schedule) == schedule
    assert is_schedule_only_want_me_to(schedule)


def test_finalize_gather_rewrites_and_arms_dogfood_dual_menu() -> None:
    session = InMemorySessionStore()
    broken = (
        "Empty evidence.\n\n**Want me to:**\n"
        "1. run a full investigation once you paste…, or\n"
        "2. `/integrations setup grafana`?"
    )
    session.cli_agent_messages = [
        ("user", "why is checkout 502?"),
        ("assistant", broken),
    ]
    text, offer = finalize_gather_investigation_offer(
        session,
        user_text="why is checkout 502?",
        assistant_text=broken,
        observation="grafana: connection refused",
    )
    assert offer is not None
    assert text.endswith("**Want me to:** run a full investigation")
    assert session.cli_agent_messages[-1] == ("assistant", text)
    assert session.pending_investigation_offer is not None
    assert "why is checkout 502?" in session.pending_investigation_offer.alert_text


def test_run_turn_dogfood_dual_menu_yes_still_starts_investigation() -> None:
    """Regression: gather dual Want-me-to must not steal bare yes from investigate."""
    session = InMemorySessionStore()
    seen: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        seen.append(text)
        if not text.startswith(INVESTIGATION_ACCEPT_MARKER):
            return ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
                response_text="",
                handoff_contents=("diagnostic:checkout_502",),
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
        # Intentionally broken closer from live dogfood.
        response_text = (
            "Checkout 502; Grafana unreachable.\n\n"
            "**Want me to:**\n"
            "1. run a full investigation once you paste the alert, or\n"
            "2. walk you through `/integrations setup grafana`?"
        )

    first = run_turn(
        "why is checkout 502?",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: _Run(),
        gather=lambda *_a, **_k: "connection refused",
        accounting=NoopTurnAccounting(),
    )

    assert session.pending_investigation_offer is not None
    shown = first.assistant_response_text or ""
    assert shown.rstrip().endswith("**Want me to:** run a full investigation")
    assert "/integrations" not in shown

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
    assert session.pending_investigation_offer is None


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
