"""Session-goal continuation prompts for an attached SessionGoal.

Leaf module: imports :mod:`core.agent_harness.session_goal.goal` only —
do not import this from ``goal`` (avoids ``py/cyclic-import``). Distinct from
:mod:`core.agent_harness.session_goal.progress` (presentation).
"""

from __future__ import annotations

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    derive_session_goal_reason,
)


def continuation_prompt(goal: SessionGoal) -> str:
    """User-visible follow-up message for the next session-goal turn."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    reason_block = f"Last progress: {reason}\n\n"
    if goal.findings:
        established = "\n".join(f"  - {item}" for item in goal.findings)
        reason_block += (
            "Already established in earlier turns of this goal — treat these as "
            "done and do not report them as unavailable:\n"
            f"{established}\n\n"
        )
    if goal.last_answer:
        reason_block += (
            "The previous turn of this goal already told the user:\n"
            f"  {goal.last_answer}\n"
            "Re-derive it if you must, but if your answer differs, say why — do "
            "not replace it with a different number silently.\n\n"
        )
    unfinished = goal.unfinished_items
    if unfinished:
        pending = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Unfinished checklist items (0-based indices):\n"
            f"{pending}\n\n"
            "Take the next unfinished item now. When you complete an item, include "
            "`session_goal:done=<index>` (comma-separate multiple). When every "
            "item is done, you may also include `session_goal:achieved`."
        )
    if goal.host_owned:
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Answer the condition directly. Do not run `/goal` as a tool. When the "
            "condition is met, include the exact tag `session_goal:achieved` in "
            "your reply (no further tool work required for a host-set goal)."
        )
    return (
        "[session_goal] Continue the active goal without asking whether to "
        f"continue. Goal: {goal.condition}\n\n"
        f"{reason_block}"
        "Take the next unfinished step now. When the goal is met after real tool "
        "work, include the exact tag `session_goal:achieved` in your reply. "
        "Do not emit that tag with no tool evidence — the host will ignore it."
    )


__all__ = [
    "continuation_prompt",
]
