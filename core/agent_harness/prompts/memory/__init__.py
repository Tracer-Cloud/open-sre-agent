"""Conversation-window and prior-investigation recall fragments for prompt assembly."""

from __future__ import annotations

from core.agent_harness.prompts.memory.conversation import (
    expand_affirmative_follow_up,
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.prompts.memory.prior_investigation import (
    PRIOR_INVESTIGATION_HANDOFF_PREFIX,
    PRIOR_INVESTIGATION_RECALL_SECONDS,
    STALE_PRIOR_INVESTIGATION_NOTE,
    build_block,
    is_prior_investigation_follow_up,
    is_within_recall_window,
    prior_investigation_headline,
    summarize,
)

__all__ = [
    "PRIOR_INVESTIGATION_HANDOFF_PREFIX",
    "PRIOR_INVESTIGATION_RECALL_SECONDS",
    "STALE_PRIOR_INVESTIGATION_NOTE",
    "build_block",
    "expand_affirmative_follow_up",
    "format_prior_action_facts",
    "format_recent_conversation",
    "is_prior_investigation_follow_up",
    "is_within_recall_window",
    "prior_investigation_headline",
    "summarize",
]
