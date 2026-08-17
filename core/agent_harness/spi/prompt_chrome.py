"""The want-me-to closer and shell prompt chrome a host renders or strips."""

from __future__ import annotations

from core.agent_harness.session.want_me_to import WANT_ME_TO_MARKER, closer_tail_from
from core.agent_harness.session_goal.goal import strip_shell_prompt_chrome

__all__ = ["WANT_ME_TO_MARKER", "closer_tail_from", "strip_shell_prompt_chrome"]
