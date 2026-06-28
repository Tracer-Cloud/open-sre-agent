"""Prompt context for the shell action core.agent."""

from __future__ import annotations

import re

from core.agent.conversation_memory import format_recent_conversation
from core.agent.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
from core.agent.prompts.envelope import PromptBlock, PromptEnvelope
from core.agent.turn_context import TurnContext

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"


def build_action_system_prompt(turn_ctx: TurnContext) -> str:
    return build_action_system_prompt_envelope(turn_ctx).render()


def build_action_system_prompt_envelope(turn_ctx: TurnContext) -> PromptEnvelope:
    return PromptEnvelope.from_blocks(
        (
            PromptBlock(
                id="action-agent-system-base",
                kind="system",
                content=_SYSTEM_PROMPT_BASE + "\n\n",
                provenance="core.agent.prompts.action_agent_system_prompt",
            ),
            PromptBlock(
                id="connected-integrations",
                kind="context",
                content=connected_integrations_block(turn_ctx),
                provenance="core.agent.turn_context",
            ),
            PromptBlock(
                id="recent-conversation",
                kind="conversation",
                content=recent_conversation_block(turn_ctx),
                provenance="core.agent.turn_context",
            ),
        ),
        separator="",
        metadata={"prompt": "action_agent_system"},
    )


def connected_integrations_block(turn_ctx: TurnContext) -> str:
    """Render which integrations are connected for this shell action turn."""
    known = turn_ctx.configured_integrations_known
    configured = turn_ctx.configured_integrations
    if known and configured:
        listing = ", ".join(sorted(str(name) for name in configured))
    elif known:
        listing = "none"
    else:
        listing = "unknown"
    gate_note = ""
    if listing in ("none", "unknown"):
        gate_note = (
            "This line gates ONLY implicit diagnostic questions (no explicit "
            "investigate/RCA/diagnose/analyze/root-cause verb). Explicit "
            "investigate instructions STILL emit investigation_start regardless.\n"
        )
    return f"CONNECTED INTEGRATIONS (this install, right now): {listing}\n{gate_note}\n"


def recent_conversation_block(turn_ctx: TurnContext) -> str:
    history = format_recent_conversation(list(turn_ctx.conversation_messages))
    return (
        "RECENT CONVERSATION (context only, oldest first; use it ONLY to resolve "
        "follow-up references in the USER MESSAGE below — do NOT re-run turns that "
        f"already completed):\n{history}\n\n"
    )


def build_action_user_message(text: str) -> str:
    return _USER_TEMPLATE.format(text=sanitize_action_text(text.strip()))


def sanitize_action_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]


__all__ = [
    "build_action_system_prompt_envelope",
    "build_action_system_prompt",
    "build_action_user_message",
    "connected_integrations_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
