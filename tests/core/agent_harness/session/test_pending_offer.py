"""Structured schedule offers — yes expands without scraping Want-me-to prose."""

from __future__ import annotations

from core.agent_harness.prompts.conversation_memory import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import PendingScheduleOffer
from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.agent_harness.tools.tool_context import ActionToolContext
from tools.interactive_shell.actions.propose_scheduled_delivery import (
    execute_propose_scheduled_delivery_tool,
)


def test_pending_offer_to_slash_omits_slack_chat_id() -> None:
    offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
    )
    assert offer.to_slash_command() == (
        "/cron add --kind daily_summary --cron 0 8 * * 1-5 "
        "--tz Europe/Amsterdam --provider slack"
    )


def test_yes_uses_pending_schedule_not_prose() -> None:
    pending = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 9 * * 1",
        timezone="UTC",
        provider="telegram",
        chat_id="-100123",
    )
    history = [
        (
            "assistant",
            "Delivered.\nWant me to: schedule this as a daily_summary every "
            "weekday at 8am to the same channel?",
        ),
    ]
    expanded = expand_affirmative_follow_up(
        "yes", history, pending_schedule=pending
    )
    assert expanded == (
        "/cron add --kind daily_summary --cron 0 9 * * 1 "
        "--tz UTC --provider telegram --chat-id -100123"
    )
    assert "1-5" not in expanded
    assert "same channel" not in expanded


def test_propose_tool_sets_session_pending_offer() -> None:
    session = InMemorySessionStore()
    ctx = ActionToolContext(session=session, console=object())
    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "daily_summary",
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": "slack",
        },
        ctx,
    )
    assert result["ok"] is True
    assert session.pending_schedule_offer is not None
    assert session.pending_schedule_offer.kind == "daily_summary"
    assert result["closer"].startswith("**Want me to:**")


def test_run_turn_consumes_pending_schedule_on_yes() -> None:
    session = InMemorySessionStore()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="daily_summary",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
    )
    seen: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        seen.append(text)
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="ok",
        )

    run_turn(
        "yes",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: None,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
    )

    assert len(seen) == 1
    assert seen[0].startswith("/cron add")
    assert session.pending_schedule_offer is None
