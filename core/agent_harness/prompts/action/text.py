"""Shell action-agent system prompt text.

The stable base lives in ``opensre_system_prompt.md`` (loaded at import time),
matching the Codex pattern of keeping the long system prompt in a markdown file
beside the loader. That file is the core OpenSRE agent prompt — model-agnostic,
not tied to a specific LLM version.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from core.agent_harness.prompts.action.multi_step_policy import (
    ACTION_CONVERSATIONAL_SESSION_GOAL_RULE,
    ACTION_LOCAL_SHELL_MULTI_STEP_RULE,
    ACTION_PRIOR_INVESTIGATION_FOLLOW_UP_RULE,
)

# When the planner should offer scheduling, given CONTEXT setup_state.
# Skill bodies (e.g. morning_report) own the procedural steps.
ACTION_SETUP_CAPACITY_SCHEDULE_RULE = (
    "- Read the setup-state block when present: if Integrations connected are "
    "not none and this turn finished a naturally recurring skill (or the user "
    "asked for recurring work), call propose_scheduled_delivery then WAIT — "
    "do not skip the offer only because schedule_count is already > 0 unless "
    "they declined or asked for a one-off only. If Integrations connected are "
    "none, do not invent a delivery channel; hand off or route to "
    "/integrations setup.\n"
)

_PROMPT_FILENAME = "opensre_system_prompt.md"


@lru_cache(maxsize=1)
def _load_system_prompt_base() -> str:
    """Return the bundled action-agent system prompt markdown."""
    path = Path(__file__).with_name(_PROMPT_FILENAME)
    return path.read_text(encoding="utf-8")


_SYSTEM_PROMPT_BASE = _load_system_prompt_base()

__all__ = (
    "ACTION_CONVERSATIONAL_SESSION_GOAL_RULE",
    "ACTION_LOCAL_SHELL_MULTI_STEP_RULE",
    "ACTION_PRIOR_INVESTIGATION_FOLLOW_UP_RULE",
    "ACTION_SETUP_CAPACITY_SCHEDULE_RULE",
    "_PROMPT_FILENAME",
    "_SYSTEM_PROMPT_BASE",
)
