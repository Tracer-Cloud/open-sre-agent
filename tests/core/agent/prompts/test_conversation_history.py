"""Tests for the shared recent-conversation context builder."""

from __future__ import annotations

from core.agent_harness.prompts.memory.conversation import (
    NO_HISTORY_PLACEHOLDER,
    format_prior_action_facts,
    format_recent_conversation,
)
from core.state import MAX_CONVERSATION_MESSAGES, MAX_CONVERSATION_TURNS


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


def test_newest_first_keeps_turn_pairs_and_puts_latest_first() -> None:
    messages = [
        ("user", "old-request"),
        ("assistant", "old-reply"),
        ("user", "new-request"),
        ("assistant", "new-reply"),
    ]
    rendered = format_recent_conversation(messages, newest_first=True)
    assert rendered == (
        "User: new-request\nAssistant: new-reply\nUser: old-request\nAssistant: old-reply"
    )


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


def test_prior_action_facts_extracts_tool_outputs_and_values() -> None:
    rendered = format_prior_action_facts(
        [
            ("user", "Can you send the weather of both hawaii and antartica to slack?"),
            (
                "assistant",
                "Hawaii: +28C\n"
                "Antarctica: -24C\n"
                'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                'slack_send_message result: {"sent": true}',
            ),
            ("user", "Write it in a more nice message and compare to london"),
            ("assistant", "London: +22C"),
            ("assistant", "Sure, paste the original message and values."),
        ]
    )

    assert "Hawaii" in rendered
    assert "Antarctica" in rendered
    assert "slack_send_message input" in rendered
    assert "London" in rendered
    assert "paste the original message" not in rendered


def test_prior_action_facts_skips_session_summary() -> None:
    """A tool-heavy summary must not consume the facts budget and starve
    live tool results."""
    summary_body = "\n".join(
        f"- assistant: tool: old_tool_{i} result: stale-summary-output-{i} " + "x" * 300
        for i in range(20)
    )
    messages = [
        ("assistant", f"Session summary:\n{summary_body}"),
        ("user", "check the queue"),
        ("assistant", "queue_depth result: important-live-fact"),
    ]

    rendered = format_prior_action_facts(messages)

    assert "important-live-fact" in rendered
    assert "stale-summary-output" not in rendered
