"""Prompt context for the shell action core.agent_harness."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.agent_harness.prompts.action_agent_system_prompt import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.conversation_memory import (
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.prompts.envelope import PromptBlock, PromptEnvelope

if TYPE_CHECKING:
    from core.agent_harness.models.turn_snapshot import TurnSnapshot

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"


def build_action_system_prompt(turn_snapshot: TurnSnapshot) -> str:
    return build_action_system_prompt_envelope(turn_snapshot).render()


def build_action_system_prompt_envelope(turn_snapshot: TurnSnapshot) -> PromptEnvelope:
    blocks = [
        PromptBlock(
            id="action-agent-system-base",
            kind="system",
            content=_SYSTEM_PROMPT_BASE + "\n\n",
            provenance="core.agent_harness.prompts.action_agent_system_prompt",
        ),
        PromptBlock(
            id="connected-integrations",
            kind="context",
            content=connected_integrations_block(turn_snapshot),
            provenance="core.agent_harness.models.turn_snapshot",
        ),
        PromptBlock(
            id="recent-conversation",
            kind="conversation",
            content=recent_conversation_block(turn_snapshot),
            provenance="core.agent_harness.models.turn_snapshot",
        ),
    ]
    action_facts = prior_action_facts_block(turn_snapshot)
    if action_facts:
        blocks.append(
            PromptBlock(
                id="prior-action-facts",
                kind="context",
                content=action_facts,
                provenance="core.agent_harness.models.turn_snapshot",
            )
        )
    return PromptEnvelope.from_blocks(
        blocks,
        separator="",
        metadata={"prompt": "action_agent_system"},
    )


def connected_integrations_block(turn_snapshot: TurnSnapshot) -> str:
    """Render which integrations are connected for this shell action turn."""
    known = turn_snapshot.configured_integrations_known
    configured = turn_snapshot.configured_integrations
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


def recent_conversation_block(turn_snapshot: TurnSnapshot) -> str:
    history = format_recent_conversation(list(turn_snapshot.conversation_messages))
    return (
        "RECENT CONVERSATION (context only, oldest first; previous assistant messages "
        "may contain shell stdout, computed values, and prior tool inputs/results. Use "
        "these as facts when resolving follow-up references in the USER MESSAGE below "
        "and when composing later tool inputs. Do NOT re-run turns that already "
        f"completed):\n{history}\n\n"
    )


def prior_action_facts_block(turn_snapshot: TurnSnapshot) -> str:
    facts = format_prior_action_facts(list(turn_snapshot.conversation_messages))
    if not facts:
        return ""
    return (
        "PRIOR ACTION FACTS (extracted from earlier persisted assistant/tool "
        "outputs; use these values when the USER MESSAGE refers to previous "
        "results, sent messages, comparisons, or 'both/that/them'. Do NOT ask "
        f"the user to paste values already listed here):\n{facts}\n\n"
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
    "prior_action_facts_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
