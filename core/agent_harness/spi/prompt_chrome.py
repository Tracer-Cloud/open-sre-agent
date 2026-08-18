"""The want-me-to closer and shell prompt chrome a host renders or strips."""

from __future__ import annotations

from core.agent_harness.prompts.rules import normalize_three_tier_spacing
from core.agent_harness.session.want_me_to import WANT_ME_TO_MARKER, closer_tail_from
from core.agent_harness.session_goal.goal import strip_shell_prompt_chrome
from core.agent_harness.turns.cohort_identity import (
    COHORT_IDENTITY_UNVERIFIED_MARK,
    reply_reports_cohort_unverified,
)

__all__ = [
    "COHORT_IDENTITY_UNVERIFIED_MARK",
    "WANT_ME_TO_MARKER",
    "closer_tail_from",
    "normalize_three_tier_spacing",
    "reply_reports_cohort_unverified",
    "strip_shell_prompt_chrome",
]
