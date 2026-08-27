"""Prompt fragments for the live task plan.

The STABLE planning instructions live in ``planning_instructions.md``. This
module renders the per-turn CURRENT PLAN block from the snapshotted plan so
transcript compaction cannot drop it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan
from core.agent_harness.task_plan.progress import format_task_plan_plain

ASK_USER_ANSWERED_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). Continue — do not sit idle. "
    "If this is the FIRST round and the answers open new discriminating "
    "questions, call ask_user_choice for ONE more scoped round. If two rounds "
    "are already answered (see the Q&A above), do NOT ask again — write the "
    "analysis now with your best hypothesis. Two rounds is the hard maximum.\n"
    "Write the analysis in structured sections, then update_plan:\n"
    "- Facts: the answers as short bullets.\n"
    "- What the signature tells us: for each fact state what it RULES OUT, not "
    "just what it is; name the narrowing in one line.\n"
    "- Hypothesis ranking: a table with columns # | Hypothesis | Why it fits | "
    "Discriminator — the single observation that confirms or rules each one out "
    "versus the others. Rank by how well current evidence fits.\n"
    "Put that analysis in update_plan(..., explanation=...) — the UI renders it "
    "under the checklist. Use headers and a table, never one dense paragraph. "
    "Do not repeat it in the assistant closing reply. "
    "Treat RECENT CONVERSATION as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Answering is the go-ahead to continue the original request. "
    "Do not invent a plan-only pause. Set ask_user_choice(plan_only_after=true) "
    "only when the original request asked not to run yet; then after answers "
    "call update_plan(plan_only=true) and leave every step pending and STOP. "
    "Otherwise set the first step in_progress and execute it now."
)

ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE = (
    "ASK USER JUST ANSWERED (this turn). This request is plan-only — answering "
    "does not authorize execution. If this is the FIRST round and the answers "
    "open new discriminating questions, call ask_user_choice for ONE more "
    "scoped round. If two rounds are already answered, do NOT ask again. "
    "Write the analysis in structured sections, then update_plan with every "
    "step pending and STOP:\n"
    "- Facts: the answers as short bullets.\n"
    "- What the signature tells us: for each fact state what it RULES OUT.\n"
    "- Hypothesis ranking: # | Hypothesis | Why it fits | Discriminator.\n"
    "Put that analysis in update_plan(..., explanation=...). "
    "Treat RECENT CONVERSATION as authoritative: preserve the original target "
    "repository and every requested output or metric. The Q&A answers refine "
    "that request; they never replace it. "
    "Do not pass plan_only=false; the host keeps the plan-only latch until the user "
    "confirms a mutating step at the execution gate."
)


_INSTRUCTIONS_FILENAME = "planning_instructions.md"


@lru_cache(maxsize=1)
def load_planning_instructions() -> str:
    """Return the bundled planning-instruction markdown."""
    path = Path(__file__).with_name(_INSTRUCTIONS_FILENAME)
    return path.read_text(encoding="utf-8")


def ask_user_answered_block(text: str, *, plan_only: bool = False) -> str:
    """Ephemeral start-now rule when this turn is structured Ask User answers."""
    if not parse_ask_user_answers(text):
        return ""
    if plan_only:
        return ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE
    return ASK_USER_ANSWERED_GUIDANCE


def current_task_plan_block(
    plan: TaskPlan | None,
    *,
    plan_only: bool = False,
) -> str:
    """Render the CURRENT PLAN block, or ``""`` when no plan is attached."""
    if plan is None or not plan.steps:
        return ""
    if plan.all_completed:
        status = "complete"
    elif plan.all_pending:
        status = "ready, nothing executed"
    else:
        status = "in progress"
    lines = [
        f"CURRENT PLAN ({status}; Plan · {plan.current_index}/{plan.total}). "
        "This is the durable record — older messages may have dropped an "
        "earlier version. Keep it current with update_plan; do not recreate "
        "it from memory.",
        format_task_plan_plain(plan),
    ]
    if plan.explanation:
        lines.append(f"explanation: {plan.explanation}")
    if plan.all_pending and not plan_only:
        lines.append(
            "Execution is authorized: set the first step to in_progress and "
            "run its tools — do not wait for the user to say go."
        )
    in_progress = next(
        (item.step for item in plan.steps if item.status is PlanStepStatus.IN_PROGRESS),
        None,
    )
    if in_progress is not None:
        lines.append(f"now: {in_progress}")
        lines.append(
            "Do not conclude this turn while a step is in_progress. "
            "Keep working that step, or ask_user_choice if facts are missing. "
            "Do not start another investigation."
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ASK_USER_ANSWERED_GUIDANCE",
    "ASK_USER_ANSWERED_PLAN_ONLY_GUIDANCE",
    "ask_user_answered_block",
    "current_task_plan_block",
    "load_planning_instructions",
]
