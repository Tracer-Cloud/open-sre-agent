"""Shell action-agent system prompt text.

The stable base lives in ``opensre_system_prompt.md`` (loaded at import time)
so the long planner prompt is editable as data and packaged with the wheel.
That file is the OpenSRE action-planner contract (handoff vs investigation,
compound turns, slash mapping) — not a coding-agent / Codex clone.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from core.agent_harness.prompts.action.multi_step_policy import (
    ACTION_CONVERSATIONAL_SESSION_GOAL_RULE,
    ACTION_LOCAL_SHELL_MULTI_STEP_RULE,
)

# When the planner should offer scheduling, given CONTEXT setup_state.
# Skill bodies (e.g. morning_report) own the procedural steps.
# The same text is inlined in opensre_system_prompt.md; tests require this
# constant to remain a substring of the loaded base.
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
    "ACTION_SETUP_CAPACITY_SCHEDULE_RULE",
    "_PROMPT_FILENAME",
    "_SYSTEM_PROMPT_BASE",
)
