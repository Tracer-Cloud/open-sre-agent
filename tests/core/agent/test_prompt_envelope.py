from __future__ import annotations

import pytest

from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.prompts import (
    PromptBlock,
    PromptEnvelope,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
)


def _ctx() -> TurnSnapshot:
    return TurnSnapshot(
        text="show connected integrations",
        conversation_messages=(("user", "hello"),),
        configured_integrations=("github",),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )


def test_prompt_envelope_renders_existing_string_prompt_without_changes() -> None:
    envelope = PromptEnvelope.from_text("line one\n\nline two")

    assert envelope.render() == "line one\n\nline two"
    assert envelope.require_block("prompt").content == "line one\n\nline two"


def test_prompt_envelope_renders_ordered_blocks_with_optional_titles() -> None:
    envelope = PromptEnvelope.from_blocks(
        (
            PromptBlock(id="rules", kind="rule", content="Follow the rules."),
            PromptBlock(
                id="cli-reference",
                kind="context",
                title="CLI reference",
                content="opensre --help",
                include_title=True,
            ),
        ),
        separator="\n\n",
    )

    assert envelope.render() == "Follow the rules.\n\n--- CLI reference ---\nopensre --help"
    assert envelope.block("cli-reference") is not None
    with pytest.raises(KeyError, match="missing"):
        envelope.require_block("missing")


def test_action_system_prompt_envelope_matches_legacy_rendering() -> None:
    ctx = _ctx()
    envelope = build_action_system_prompt_envelope(ctx)

    assert [block.id for block in envelope.blocks] == [
        "action-agent-system-base",
        "connected-integrations",
        "recent-conversation",
    ]
    assert envelope.require_block("connected-integrations").kind == "context"
    assert envelope.require_block("recent-conversation").kind == "conversation"
    assert envelope.render() == build_action_system_prompt(ctx)
