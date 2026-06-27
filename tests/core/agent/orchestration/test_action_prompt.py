"""Unit tests for shell action-agent prompt context."""

from __future__ import annotations

from core.agent.context import TurnContext
from core.agent.conversation_history import NO_HISTORY_PLACEHOLDER
from core.agent.prompts import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    connected_integrations_block,
    recent_conversation_block,
)


def _ctx(
    *,
    messages: list[tuple[str, str]] | None = None,
    integrations: tuple[str, ...] = (),
    integrations_known: bool = False,
) -> TurnContext:
    return TurnContext(
        text="",
        conversation_messages=tuple(messages or []),
        configured_integrations=integrations,
        configured_integrations_known=integrations_known,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )


def test_recent_conversation_block_contains_history_lines() -> None:
    ctx = _ctx(
        messages=[
            ("user", "how can I remove github integration"),
            ("assistant", "Use /integrations remove github or /integrations list."),
        ]
    )
    block = recent_conversation_block(ctx)
    assert "RECENT CONVERSATION" in block
    assert "User: how can I remove github integration" in block
    assert "Assistant: Use /integrations remove github or /integrations list." in block


def test_recent_conversation_block_placeholder_without_history() -> None:
    assert NO_HISTORY_PLACEHOLDER in recent_conversation_block(_ctx())


def test_system_prompt_documents_followup_resolution() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "do both" in prompt
    assert "recent conversation" in prompt
    assert "assistant_handoff" in prompt


def test_system_prompt_requires_same_response_for_slash_then_investigation() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "connect with /remote and then investigate" in prompt
    assert "same planner response" in prompt
    assert "do not stop after the slash command" in prompt
    assert "valid investigation payload" in prompt


def test_system_prompt_keeps_bare_alert_blob_as_handoff() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "a bare pasted alert blob with no instruction remains assistant_handoff" in prompt
    assert "pasted alert blob / bare incident statement" in prompt
    assert "with no\ninstruction" in prompt
    assert "not such a question — hand it off" in prompt


def test_system_prompt_preserves_bare_numeric_synthetic_mapping() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "run synthetic test 005 now" in prompt
    assert 'scenario="005-failover"' in prompt
    assert "never substitute a different numbered" in prompt


def test_connected_integrations_block_renders_state() -> None:
    assert "unknown" in connected_integrations_block(_ctx())

    none_block = connected_integrations_block(_ctx(integrations=(), integrations_known=True))
    assert "none" in none_block
    assert "explicit investigate instructions still emit investigation_start" in none_block.lower()

    listed = connected_integrations_block(
        _ctx(
            integrations=("sentry", "github", "posthog_mcp"),
            integrations_known=True,
        )
    )
    assert "github, posthog_mcp, sentry" in listed


def test_action_system_prompt_includes_context_blocks() -> None:
    prompt = build_action_system_prompt(
        _ctx(
            messages=[("user", "hello")],
            integrations=("github",),
            integrations_known=True,
        )
    )
    assert "CONNECTED INTEGRATIONS (this install, right now): github" in prompt
    assert "RECENT CONVERSATION" in prompt
