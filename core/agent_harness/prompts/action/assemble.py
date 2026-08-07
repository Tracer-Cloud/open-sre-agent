"""Prompt context for the shell action core.agent_harness."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.memory.conversation import (
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.prompts.skills.loader import load_skills_index
from platform.harness_ports import action_prompt_vendor_fragments

if TYPE_CHECKING:
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"


def build_action_system_prompt(turn_snapshot: TurnSnapshot) -> str:
    return build_action_system_prompt_envelope(turn_snapshot).render()


def build_action_system_prompt_envelope(turn_snapshot: TurnSnapshot) -> PromptEnvelope:
    blocks = [
        PromptBlock(
            id="action-agent-system-base",
            kind=PromptBlockKind.SYSTEM,
            tier=PromptTier.STABLE,
            # Trailing separators stay in the block; avoid ``base + "\n\n"`` which
            # copies the entire stable prompt body on every turn.
            content="".join((_SYSTEM_PROMPT_BASE, "\n\n")),
            provenance="core.agent_harness.prompts.action.text",
        ),
    ]
    vendor_fragments = action_prompt_vendor_fragments()
    if vendor_fragments:
        blocks.append(
            PromptBlock(
                id="action-agent-vendor-fragments",
                kind=PromptBlockKind.RULE,
                tier=PromptTier.STABLE,
                content="".join((vendor_fragments, "\n\n")),
                provenance="platform.harness_ports.action_prompt_vendor_fragments",
            )
        )
    skills_index = load_skills_index()
    if skills_index:
        blocks.append(
            PromptBlock(
                id="action-agent-skills",
                kind=PromptBlockKind.RULE,
                tier=PromptTier.STABLE,
                content=skills_index,
                provenance="core.agent_harness.prompts.skills",
            )
        )
    if turn_snapshot.setup_state:
        blocks.append(
            PromptBlock(
                id="action-agent-setup-state",
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.CONTEXT,
                content=turn_snapshot.setup_state,
                provenance="platform.setup_state",
            )
        )
    blocks.append(
        PromptBlock(
            id="connected-integrations",
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            content=connected_integrations_block(turn_snapshot),
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    # Volatile before ephemeral so render_cached + render_ephemeral reassemble
    # into render() and the cache breakpoint can sit after memory.
    memory_block = long_term_memory_block()
    if memory_block:
        blocks.append(
            PromptBlock(
                id="long-term-memory",
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.VOLATILE,
                content=memory_block,
                provenance="core.domain.memory",
            )
        )
    blocks.append(
        PromptBlock(
            id="recent-conversation",
            kind=PromptBlockKind.CONVERSATION,
            tier=PromptTier.EPHEMERAL,
            content=recent_conversation_block(turn_snapshot),
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    action_facts = prior_action_facts_block(turn_snapshot)
    if action_facts:
        blocks.append(
            PromptBlock(
                id="prior-action-facts",
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.EPHEMERAL,
                content=action_facts,
                provenance="core.agent_harness.turns.turn_snapshot",
            )
        )
    recovery = interrupted_turn_recovery_block(turn_snapshot)
    if recovery:
        # Ephemeral: the note rides exactly one turn (popped from the session
        # at snapshot time), so the cached prompt half stays byte-identical.
        blocks.append(
            PromptBlock(
                id="interrupted-turn-recovery",
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.EPHEMERAL,
                content=recovery,
                provenance="core.agent_harness.session.persistence.wal_recovery",
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
    # Listing does not gate diagnostic→investigation (Phase 1b): why/figure-out
    # always hand off for gather + Want-me-to; explicit investigate always
    # dispatches. The list only tells the planner which sources gather can use.
    gate_note = (
        "This listing does NOT gate diagnostic→investigation. Cause/why / "
        "figure-out questions → assistant_handoff + gather + Want-me-to "
        "investigate offer. Explicit investigate/RCA/diagnose/analyze/"
        "root-cause verbs → investigation_start ALWAYS (even when this line "
        "is none).\n"
    )
    return f"CONNECTED INTEGRATIONS (this install, right now): {listing}\n{gate_note}\n"


def recent_conversation_block(turn_snapshot: TurnSnapshot) -> str:
    # Newest-first: this block rides after the literal user message, and
    # context_budget shrinks with text[:keep]. Chronological (oldest-first)
    # order put the latest turns at the truncated tail — follow-ups then saw
    # only stale chatter. Newest-first drops the oldest turns under pressure.
    history = format_recent_conversation(
        list(turn_snapshot.conversation_messages),
        newest_first=True,
    )
    return (
        "RECENT CONVERSATION (context only, newest first; previous assistant messages "
        "may contain shell stdout, computed values, and prior tool inputs/results. Use "
        "these as facts when resolving follow-up references in the USER MESSAGE above "
        "and when composing later tool inputs. Do NOT re-run turns that already "
        f"completed):\n{history}\n\n"
    )


def prior_action_facts_block(turn_snapshot: TurnSnapshot) -> str:
    facts = format_prior_action_facts(list(turn_snapshot.conversation_messages))
    if not facts:
        return ""
    return (
        "PRIOR ACTION FACTS (newest first; extracted from earlier persisted "
        "assistant/tool outputs; use these values when the USER MESSAGE refers "
        "to previous results, sent messages, comparisons, or 'both/that/them'. "
        "Do NOT ask the user to paste values already listed here):\n"
        f"{facts}\n\n"
    )


def interrupted_turn_recovery_block(turn_snapshot: TurnSnapshot) -> str:
    """Render the WAL recovery note for the first action turn after ``/resume``."""
    note = turn_snapshot.recovery_note
    if not note:
        return ""
    return (
        "INTERRUPTED-TURN RECOVERY (write-ahead log of the resumed session; "
        "applies to THIS turn):\n"
        f"{note}\n\n"
    )


def long_term_memory_block() -> str:
    """Inject stored memory facts into every action-agent turn when available."""
    from core.domain.memory import (
        ensure_memory_store,
        memory_available_here,
        render_prompt_index,
    )

    if not memory_available_here():
        return ""
    ensure_memory_store()
    rendered = render_prompt_index()
    if not rendered:
        return ""
    return (
        "LONG-TERM MEMORY (durable facts from ~/.opensre/memory — injected into "
        "every turn). Use listed facts when planning; when the USER MESSAGE "
        "contains a new useful durable fact, call memory_remember in this turn "
        "even if they never said remember/save — do not wait for special phrasing. "
        "Prefer updating an existing name over near-duplicates:\n"
        f"{rendered}\n\n"
    )


def build_action_user_message(text: str, *, prefix: str = "") -> str:
    """Wrap the user turn; optional ``prefix`` carries ephemeral system halves.

    ``prefix`` is where per-turn conversation / prior-action-facts land once the
    action driver opts into :meth:`PromptEnvelope.render_cached` — the same text
    the model used to see at the end of ``system``, moved here so the cached
    system prefix stays byte-stable across turns.

    Layout (required by head-preserving truncation ``text[:keep]``):

    1. Literal user message first — never drop the active request.
    2. Ephemeral history next, newest-first — when the combined turn is over
       budget, the tail cut removes the oldest chatter, not the latest turns
       the follow-up is referring to.
    """
    body = _USER_TEMPLATE.format(text=sanitize_action_text(text.strip()))
    if not prefix:
        return body
    # Ephemeral history can be large; join avoids an intermediate f-string copy.
    return "".join((body, "\n", prefix))


def sanitize_action_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]


__all__ = [
    "build_action_system_prompt_envelope",
    "build_action_system_prompt",
    "build_action_user_message",
    "connected_integrations_block",
    "interrupted_turn_recovery_block",
    "long_term_memory_block",
    "prior_action_facts_block",
    "recent_conversation_block",
    "sanitize_action_text",
]
