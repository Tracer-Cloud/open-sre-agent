"""The assistant prompt carries the operator's real setup as CONTEXT-tier facts."""

from __future__ import annotations

from core.agent_harness.prompts.assistant import (
    build_assistant_system_prompt_envelope,
)
from core.agent_harness.prompts.envelope import PromptTier

_SETUP_BLOCK_ID = "assistant-setup-state"
# A value the model could not produce on its own, so finding it in the rendered
# prompt proves it travelled from the caller rather than being invented.
_MARKER = "acme_pager_duty"


def _envelope(setup_state: str, surface: str = "interactive_shell"):
    return build_assistant_system_prompt_envelope(
        reference="",
        history="",
        setup_state=setup_state,
        surface=surface,
    )


def test_setup_state_reaches_the_rendered_prompt() -> None:
    # Arrange / Act
    envelope = _envelope(f"Integrations connected: {_MARKER}\n")

    # Assert
    assert _MARKER in envelope.render()


def test_setup_state_sits_in_the_context_tier() -> None:
    # Arrange / Act: the tier decides cache behaviour, so it is the property
    # under test — setup changes between sessions, not within a turn.
    envelope = _envelope(f"Integrations connected: {_MARKER}\n")

    # Assert
    block = next(b for b in envelope.blocks if b.id == _SETUP_BLOCK_ID)
    assert block.tier is PromptTier.CONTEXT


def test_gateway_surface_does_not_leak_one_installs_setup() -> None:
    # Arrange: a shared chat surface serves many members, so one operator's
    # connected integrations are not theirs to read.
    envelope = _envelope(f"Integrations connected: {_MARKER}\n", surface="gateway")

    # Assert: the marker must not appear anywhere in the gateway prompt.
    assert _MARKER not in envelope.render()


def test_absent_setup_state_adds_no_block() -> None:
    # Arrange / Act: callers that cannot compute the state pass nothing.
    envelope = _envelope("")

    # Assert: no empty header, which would read as "nothing configured".
    assert all(block.id != _SETUP_BLOCK_ID for block in envelope.blocks)
