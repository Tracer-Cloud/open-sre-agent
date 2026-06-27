"""Prompt context for the shell action agent."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from interactive_shell.harness.llm_context.action_prompt_text import SYSTEM_PROMPT_BASE
from interactive_shell.harness.llm_context.conversation_history import format_recent_conversation

if TYPE_CHECKING:
    from interactive_shell.harness.agent_context import AgentContext

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"


def build_action_system_prompt(agent_ctx: AgentContext) -> str:
    return (
        SYSTEM_PROMPT_BASE
        + "\n\n"
        + connected_integrations_block(agent_ctx)
        + recent_conversation_block(agent_ctx)
    )


def connected_integrations_block(agent_ctx: AgentContext) -> str:
    """Render which integrations are connected for this shell action turn."""
    known = agent_ctx.configured_integrations_known
    configured = agent_ctx.configured_integrations
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


def recent_conversation_block(agent_ctx: AgentContext) -> str:
    history = format_recent_conversation(list(agent_ctx.conversation_messages))
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
    "build_action_system_prompt",
    "build_action_user_message",
    "connected_integrations_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
