"""Tests for the shared recent-conversation context builder."""

from __future__ import annotations

from core.agent.conversation_memory import (
    MAX_CONVERSATION_MESSAGES,
    MAX_CONVERSATION_TURNS,
    NO_HISTORY_PLACEHOLDER,
    format_recent_conversation,
)


def test_placeholder_when_no_history() -> None:
    assert format_recent_conversation([]) == NO_HISTORY_PLACEHOLDER


def test_placeholder_when_empty_tuple() -> None:
    assert format_recent_conversation(()) == NO_HISTORY_PLACEHOLDER


def test_renders_user_and_assistant_labels_in_order() -> None:
    messages = [
        ("user", "how can I remove github integration"),
        ("assistant", "Use /integrations remove github or verify with /integrations list."),
        ("user", "do both for me"),
    ]
    rendered = format_recent_conversation(messages)
    assert rendered == (
        "User: how can I remove github integration\n"
        "Assistant: Use /integrations remove github or verify with /integrations list.\n"
        "User: do both for me"
    )


def test_caps_to_max_turns() -> None:
    # 20 turns (40 messages); only the last MAX_CONVERSATION_TURNS turns survive.
    messages: list[tuple[str, str]] = []
    for i in range(20):
        messages.append(("user", f"u{i}"))
        messages.append(("assistant", f"a{i}"))
    rendered = format_recent_conversation(messages)

    lines = rendered.splitlines()
    assert len(lines) == MAX_CONVERSATION_MESSAGES
    # Oldest retained turn is turn (20 - MAX_CONVERSATION_TURNS).
    first_kept = 20 - MAX_CONVERSATION_TURNS
    assert lines[0] == f"User: u{first_kept}"
    assert lines[-1] == "Assistant: a19"


def test_custom_max_turns() -> None:
    messages = [("user", "u0"), ("assistant", "a0"), ("user", "u1"), ("assistant", "a1")]
    rendered = format_recent_conversation(messages, max_turns=1)
    assert rendered == "User: u1\nAssistant: a1"


def test_zero_max_turns_returns_placeholder() -> None:
    messages = [("user", "u0"), ("assistant", "a0")]
    assert format_recent_conversation(messages, max_turns=0) == NO_HISTORY_PLACEHOLDER


def test_skips_malformed_entries() -> None:
    messages: list[tuple[str, str]] = [("user", "hi"), ("assistant", "hello")]
    # Inject a malformed entry that does not unpack into (role, content).
    malformed: list[object] = list(messages)
    malformed.insert(1, ("only-one-element",))
    rendered = format_recent_conversation(malformed)  # type: ignore[arg-type]
    assert rendered == "User: hi\nAssistant: hello"
