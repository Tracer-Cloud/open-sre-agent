"""Live task-plan overlay for the interactive shell.

The plan renders in one place: an ANSI overlay pinned above the prompt, at the
bottom of the screen. Before execution the whole checklist is shown; once work
starts it collapses to the header plus the current step so the prompt region
stays short while tool output streams above it. The plan is never dumped into
the transcript.
"""

from __future__ import annotations

from core.agent_harness.spi.task_plan import (
    PLAN_STATUS_GLYPH,
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    format_plan_header,
    parse_task_plan,
)
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width

_STEP_INDENT = "  "


def task_plan_from_tool_args(args: dict[str, object]) -> TaskPlan | None:
    """Parse a plan from an ``update_plan`` tool-call payload."""
    if not isinstance(args, dict):
        return None
    plan, _error = parse_task_plan(args)
    return plan


def _overlay_line(text: str, style: str, width: int) -> str:
    """One ANSI overlay row: strip controls via ``clip_prompt_text``, then style.

    ``clip_prompt_text`` is the single sanitize+truncate boundary for raw ANSI
    overlays (including ``PlanStep`` instances built outside ``parse_task_plan``).
    """
    return f"{style}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"


def _step_overlay_line(item: PlanStep, width: int) -> str:
    step = item.step
    glyph = PLAN_STATUS_GLYPH[item.status]
    if item.status is PlanStepStatus.IN_PROGRESS:
        return _overlay_line(
            f"{glyph} {step}",
            f"{ui_theme.ANSI_BOLD}{ui_theme.TEXT_ANSI}",
            width,
        )
    if item.status is PlanStepStatus.COMPLETED:
        clipped = clip_prompt_text(step, max(width - 2, 1))
        return (
            f"{ui_theme.HIGHLIGHT_ANSI}{glyph} {ui_theme.ANSI_RESET}"
            f"{ui_theme.DIM_ANSI}{clipped}{ui_theme.ANSI_RESET}"
        )
    return _overlay_line(f"{glyph} {step}", ui_theme.DIM_ANSI, width)


def _indented_step_overlay_line(item: PlanStep, width: int) -> str:
    """A step overlay row indented under the header."""
    return _STEP_INDENT + _step_overlay_line(item, max(width - len(_STEP_INDENT), 1))


def task_plan_overlay_ansi(plan: TaskPlan) -> str:
    """ANSI plan overlay pinned above the prompt: the whole checklist, indented
    under its header, with ✓ done / ● current / ○ pending.

    Every step is shown at all times — before and during execution — so the user
    always sees progress across the full plan, at the bottom above the action bar.
    """
    width = prompt_line_width()
    header = _overlay_line(format_plan_header(plan), ui_theme.SECONDARY_ANSI, width)
    rows = [header, *(_indented_step_overlay_line(item, width) for item in plan.steps)]
    return "\n".join(rows)


__all__ = [
    "task_plan_from_tool_args",
    "task_plan_overlay_ansi",
]
