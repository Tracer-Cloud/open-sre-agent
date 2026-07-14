"""Tests for Slack thread → session history seeding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import gateway.slack.thread_history as thread_history
from gateway.slack.thread_history import (
    messages_from_slack_thread,
    seed_session_from_slack_thread,
    session_needs_thread_seed,
)


def test_session_needs_seed_for_bare_yes_without_want_me_to() -> None:
    session = SimpleNamespace(cli_agent_messages=[("user", "hi"), ("assistant", "hello")])
    assert session_needs_thread_seed(session, "yes") is True


def test_session_skips_seed_when_want_me_to_already_present() -> None:
    session = SimpleNamespace(
        cli_agent_messages=[
            ("assistant", "Want me to: group them by title, or pull engineers?"),
        ]
    )
    assert session_needs_thread_seed(session, "yes") is False


def test_session_needs_seed_for_restated_yes() -> None:
    session = SimpleNamespace(cli_agent_messages=[])
    assert session_needs_thread_seed(
        session, 'you asked a question: "want me to:" and I replied yes'
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
    mapped = messages_from_slack_thread(
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
    n = seed_session_from_slack_thread(
        session, channel_id="C1", thread_ts="1.0", exclude_ts="1.2"
    )
    assert n == 2
    assert session.cli_agent_messages[1][1] == "Want me to: list titles?"
