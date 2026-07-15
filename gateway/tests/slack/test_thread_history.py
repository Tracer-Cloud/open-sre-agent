"""Tests for Slack thread → session history seeding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import gateway.slack.thread_history as thread_history


def test_session_needs_seed_for_bare_yes() -> None:
    assert thread_history.session_needs_thread_seed("yes") is True


def test_affirmative_always_reseeds_even_with_prior_offer() -> None:
    # Re-seeding pulls the complete current thread, so a repeated affirmative
    # resolves against the LATEST offer rather than stale session state.
    assert thread_history.session_needs_thread_seed("yes") is True


def test_any_threaded_reply_seeds_regardless_of_wording() -> None:
    # "do that", "the first one", etc. must seed without a phrase list.
    assert thread_history.session_needs_thread_seed("do that", is_reply=True) is True
    assert thread_history.session_needs_thread_seed("the first one", is_reply=True) is True


def test_new_top_level_mention_does_not_seed() -> None:
    assert thread_history.session_needs_thread_seed("who is on the team?", is_reply=False) is False


def test_session_needs_seed_for_restated_yes() -> None:
    assert thread_history.session_needs_thread_seed(
        'you asked a question: "want me to:" and I replied yes'
    )


def test_messages_from_slack_thread_maps_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        thread_history,
        "resolve_bot_token",
        lambda: (SimpleNamespace(bot_token="xoxb-x"), ""),
    )
    monkeypatch.setattr(
        thread_history,
        "fetch_channel_messages",
        lambda *_a, **_k: (
            [
                {"user": "U1", "ts": "1.0", "text": "who is on the team?"},
                {
                    "user": "UBOT",
                    "ts": "1.1",
                    "text": (
                        "I found: 12 members.\n\n"
                        "Want me to: group them by title, or pull just the engineering folks?"
                    ),
                },
                {"user": "U1", "ts": "1.2", "text": "yes"},
            ],
            "",
        ),
    )
    mapped = thread_history.messages_from_slack_thread(
        channel_id="C1",
        thread_ts="1.0",
        exclude_ts="1.2",
        bot_user_id="UBOT",
    )
    assert mapped == [
        ("user", "who is on the team?"),
        (
            "assistant",
            "I found: 12 members.\n\n"
            "Want me to: group them by title, or pull just the engineering folks?",
        ),
    ]


def test_seed_session_writes_cli_agent_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        thread_history,
        "messages_from_slack_thread",
        lambda **_k: [
            ("user", "who is on the team?"),
            ("assistant", "Want me to: list titles?"),
        ],
    )
    session: Any = SimpleNamespace(cli_agent_messages=[])
    n = thread_history.seed_session_from_slack_thread(
        session, channel_id="C1", thread_ts="1.0", exclude_ts="1.2"
    )
    assert n == 2
    assert session.cli_agent_messages[1][1] == "Want me to: list titles?"


def test_empty_session_yes_after_thread_seed_expands_dual_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live dogfood: empty gateway session + Slack thread Want me to + bare yes.

    Replays the AWS failure where ``yes`` (and a restated ``I replied yes``)
    forgot the prior dual ``Want me to: A, or B?`` offer and fell through to
    investigate onboarding. Seeding from the thread then expanding must yield
    an actionable ``do both`` request.
    """
    from core.agent_harness.prompts.conversation_memory import expand_affirmative_follow_up

    offer = (
        "I found: the team has 12 members in the connected Slack workspace.\n\n"
        "Here's what that looks like:\n• vincent — Vincent — Co-founder\n\n"
        "Want me to: group them by title, or pull just the engineering folks?"
    )
    monkeypatch.setattr(
        thread_history,
        "messages_from_slack_thread",
        lambda **_k: [
            ("user", "who is on the team?"),
            ("assistant", offer),
        ],
    )

    session: Any = SimpleNamespace(cli_agent_messages=[])
    assert thread_history.session_needs_thread_seed("yes") is True
    assert (
        thread_history.seed_session_from_slack_thread(
            session, channel_id="C0BJ1D4LZDE", thread_ts="1.0", exclude_ts="1.2"
        )
        == 2
    )

    expanded_yes = expand_affirmative_follow_up(
        "[Slack channel_id=C0BJ1D4LZDE thread_ts=1.0]\nyes",
        session.cli_agent_messages,
    )
    assert expanded_yes.startswith("[Slack channel_id=C0BJ1D4LZDE")
    assert (
        "Yes — please do both — group them by title; and pull just the engineering folks."
        in expanded_yes
    )

    restated = expand_affirmative_follow_up(
        'you asked a question: "want me to:" and I replied yes',
        session.cli_agent_messages,
    )
    assert restated.startswith("Yes — please do both — group them by title")
    assert "pull just the engineering folks" in restated
