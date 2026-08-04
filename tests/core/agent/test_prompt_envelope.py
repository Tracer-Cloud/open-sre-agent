from __future__ import annotations

import pytest

from core.agent_harness.prompts import (
    PromptBlock,
    PromptEnvelope,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


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

    # "action-agent-vendor-fragments" carries integration-owned prompt recipes
    # (e.g. Slack/GitHub action routing) registered via
    # platform.harness_ports.register_action_prompt_fragment — see
    # integrations/harness_adapters.py. It renders empty (and is absent from
    # this id list) when no fragments are registered.
    assert [block.id for block in envelope.blocks] == [
        "action-agent-system-base",
        "action-agent-vendor-fragments",
        "action-agent-skills",
        "connected-integrations",
        "recent-conversation",
    ]
    assert envelope.require_block("action-agent-vendor-fragments").kind == "rule"
    assert envelope.require_block("action-agent-skills").kind == "rule"
    assert envelope.require_block("connected-integrations").kind == "context"
    assert envelope.require_block("recent-conversation").kind == "conversation"
    assert envelope.render() == build_action_system_prompt(ctx)


def _turn(messages: list[tuple[str, str]]) -> TurnSnapshot:
    """A snapshot differing from another only in conversation history."""
    return TurnSnapshot(
        text="show connected integrations",
        conversation_messages=tuple(messages),
        configured_integrations=("github",),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )


def test_the_cached_prefix_is_byte_identical_across_turns() -> None:
    """Two turns in one session must share a byte-identical cacheable prefix.

    Anthropic caches the request prefix up to the ``cache_control`` marker. A
    system prompt whose tail changes every turn invalidates everything before
    it, so the whole base+skills prefix is re-sent at full price on every turn
    rather than read from cache. This is the property that makes caching work.
    """
    # Arrange
    first = _turn([("user", "hello")])
    second = _turn([("user", "hello"), ("assistant", "hi"), ("user", "and again")])

    # Act
    first_cached = build_action_system_prompt_envelope(first).render_cached()
    second_cached = build_action_system_prompt_envelope(second).render_cached()

    # Assert
    assert first_cached == second_cached


def test_per_turn_conversation_never_reaches_the_cached_prefix() -> None:
    """A distinctive utterance must not appear in the cached half.

    Anything that leaks here silently costs a full-price re-send of the entire
    prefix, which no other test would catch.
    """
    # Arrange
    marker = "zzmarker-utterance-that-must-not-be-cached"
    envelope = build_action_system_prompt_envelope(_turn([("user", marker)]))

    # Act
    cached = envelope.render_cached()
    ephemeral = envelope.render_ephemeral()

    # Assert
    assert marker not in cached
    assert marker in ephemeral


def test_the_split_halves_reassemble_into_the_unchanged_render() -> None:
    """``render()`` output must not move while the halves are introduced.

    Stated as the general contract — join the non-empty halves with the
    envelope's own separator — so the assertion holds for any envelope, not
    only one that happens to separate with the empty string.
    """
    # Arrange
    envelope = build_action_system_prompt_envelope(_turn([("user", "hello")]))

    # Act
    cached, ephemeral = envelope.render_split()
    rejoined = envelope.separator.join(half for half in (cached, ephemeral) if half)

    # Assert
    assert rejoined == envelope.render()


def test_every_block_declares_which_tier_it_belongs_to() -> None:
    """A block with no tier would silently land in the cached prefix."""
    # Arrange
    envelope = build_action_system_prompt_envelope(_turn([("user", "hello")]))

    # Act
    tiers = {block.id: block.tier for block in envelope.blocks}

    # Assert
    assert tiers == {
        "action-agent-system-base": "stable",
        "action-agent-vendor-fragments": "stable",
        "action-agent-skills": "stable",
        "connected-integrations": "context",
        "recent-conversation": "ephemeral",
    }


def test_the_action_envelope_exposes_a_stable_half_the_provider_can_cache() -> None:
    """The split is what a provider needs to place a cache breakpoint.

    The driver still sends ``render()`` as one string, so nothing is cached yet.
    This pins the half that a second ``cache_control`` block will mark once the
    split reaches the provider boundary, and proves it is worth marking: it is
    the overwhelming majority of the prompt and identical between turns.
    """
    # Arrange
    first = _turn([("user", "hello")])
    second = _turn([("user", "hello"), ("assistant", "hi"), ("user", "again")])

    # Act
    first_cached, first_ephemeral = build_action_system_prompt_envelope(first).render_split()
    second_cached, _ = build_action_system_prompt_envelope(second).render_split()

    # Assert
    assert first_cached == second_cached
    assert len(first_cached) > 20 * len(first_ephemeral)


def test_the_rendered_prompt_is_unchanged_by_the_split() -> None:
    """The driver's output must be byte-identical to before tiers existed.

    Tiers are metadata until a caller opts in. If this drifts, the split has
    silently changed what every model reads.
    """
    # Arrange
    snapshot = _turn([("user", "hello")])

    # Act
    rendered = build_action_system_prompt_envelope(snapshot).render()

    # Assert
    assert rendered == build_action_system_prompt(snapshot)
