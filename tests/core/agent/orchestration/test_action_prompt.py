"""Unit tests for shell action-agent prompt context."""

from __future__ import annotations

from core.agent_harness.models.turn_snapshot import TurnSnapshot
from core.agent_harness.prompts import (
    _SYSTEM_PROMPT_BASE,
    build_action_system_prompt,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
)
from core.agent_harness.prompts.conversation_memory import NO_HISTORY_PLACEHOLDER


def _ctx(
    *,
    messages: list[tuple[str, str]] | None = None,
    integrations: tuple[str, ...] = (),
    integrations_known: bool = False,
) -> TurnSnapshot:
    return TurnSnapshot(
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


def test_prior_action_facts_block_surfaces_telegram_followup_values() -> None:
    ctx = _ctx(
        messages=[
            ("user", "Can you send the weather of both hawaii and antartica to slack?"),
            (
                "assistant",
                "Hawaii: +28C\n"
                "Antarctica: -24C\n"
                'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                'slack_send_message result: {"sent": true}',
            ),
            ("user", "Write it in a nicer message and compare to London"),
            ("assistant", "London: +22C"),
        ]
    )

    block = prior_action_facts_block(ctx)
    assert "PRIOR ACTION FACTS" in block
    assert "Hawaii: +28C" in block
    assert "Antarctica: -24C" in block
    assert "London: +22C" in block
    assert "slack_send_message input" in block


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


def test_system_prompt_maps_setup_requests_to_slash_invoke() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    assert "configure, connect, set up, add, or enable" in prompt
    assert 'slash_invoke(command="/integrations", args=["setup", "<service>"])' in prompt
    assert 'slash_invoke(command="/mcp", args=["connect", "<server>"])' in prompt
    assert "do not hand off just to tell the user" in prompt


def test_system_prompt_hands_off_natural_language_slash_status_questions() -> None:
    prompt = _SYSTEM_PROMPT_BASE.lower()
    compact_prompt = " ".join(prompt.split())
    assert 'literal slash text like "/model"' in prompt
    assert '"run /model show"' in prompt
    assert "natural-language questions about the active model/provider" in compact_prompt
    assert "which model is being used now?" in compact_prompt
    assert "what tools can you use?" in compact_prompt
    assert "what is my session status?" in compact_prompt
    assert "must use assistant_handoff" in compact_prompt
    assert "unless a read-only discovery exception below explicitly maps" in compact_prompt
    assert (
        "do not run a slash command just because the command can display related information"
        in compact_prompt
    )
    assert "for model/provider shell-state questions specifically" in compact_prompt
    assert "unless the user explicitly typed a slash command" in compact_prompt
    assert "current llm settings in its environment context" in compact_prompt


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
