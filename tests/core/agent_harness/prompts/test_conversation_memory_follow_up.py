"""Affirmative follow-ups must resolve prior Want me to: offers (Slack 'yes')."""

from __future__ import annotations

from core.agent_harness.prompts.conversation_memory import expand_affirmative_follow_up
from core.agent_harness.turns.headless_adapters import InMemorySessionStore, NoopTurnAccounting
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult


def test_expands_yes_after_roster_want_me_to_offer() -> None:
    history = [
        ("user", "[Slack channel_id=C1]\nwho is on the team?"),
        (
            "assistant",
            "I found: the Slack team has 12 visible members.\n\n"
            "Here's what that looks like:\n• vincent\n• yauhen\n\n"
            "Want me to: list their display names and titles, too?",
        ),
    ]
    expanded = expand_affirmative_follow_up("[Slack channel_id=C1]\nyes", history)
    assert expanded.startswith("[Slack channel_id=C1]")
    assert "Yes — please list their display names and titles, too." in expanded
    assert expanded.strip().endswith(".")


def test_expands_dual_or_offer_into_do_both() -> None:
    history = [
        (
            "assistant",
            "Want me to: group them by title, or pull just the engineering folks?",
        ),
    ]
    expanded = expand_affirmative_follow_up("yes", history)
    assert "do both — group them by title; and pull just the engineering folks." in expanded


def test_expands_restated_yes_after_want_me_to() -> None:
    history = [
        ("assistant", "Want me to: group them by title, or pull just the engineering folks?"),
    ]
    expanded = expand_affirmative_follow_up(
        'you asked a question: "want me to:" and I replied yes',
        history,
    )
    assert expanded.startswith("Yes — please do both")


def test_leaves_non_affirmative_unchanged() -> None:
    history = [
        ("assistant", "Want me to: list their display names and titles, too?"),
    ]
    text = "[Slack channel_id=C1]\nwho else is online?"
    assert expand_affirmative_follow_up(text, history) == text


def test_leaves_affirmative_unchanged_without_want_me_to() -> None:
    history = [("assistant", "Slack is connected.")]
    assert expand_affirmative_follow_up("yes", history) == "yes"


def test_expands_bare_sure_without_slack_prefix() -> None:
    history = [
        ("assistant", "**Want me to:** dig into the top Sentry issue?"),
    ]
    assert expand_affirmative_follow_up("sure", history) == (
        "Yes — please dig into the top Sentry issue."
    )


def test_run_turn_expands_yes_before_execute_actions() -> None:
    """Gateway Slack 'yes' must not reach the action agent as a bare affirmative."""
    session = InMemorySessionStore()
    session.cli_agent_messages = [
        ("user", "[Slack channel_id=C1]\nwho is on the team?"),
        (
            "assistant",
            "I found: 12 members.\n\nWant me to: list their display names and titles, too?",
        ),
    ]
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
        "[Slack channel_id=C1]\nyes",
        session,
        execute_actions=execute_actions,
        answer=lambda *_a, **_k: None,
        gather=lambda *_a, **_k: None,
        accounting=NoopTurnAccounting(),
    )

    assert len(seen) == 1
    assert "Yes — please list their display names and titles, too." in seen[0]
