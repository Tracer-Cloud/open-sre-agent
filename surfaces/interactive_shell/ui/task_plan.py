"""Live task-plan checklist for the interactive shell.

The transcript prints a dim toast when the agent revises the plan, and the
full checklist once when every step is still pending. The live prompt overlay
is only the header plus the current step — a six-step list in the prompt
region is reprinted into scrollback on every ``patch_stdout`` tool line.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.task_plan import (
    PLAN_STATUS_GLYPH,
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    format_plan_header,
    parse_task_plan,
)
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width
from surfaces.interactive_shell.ui.streaming import render_markdown_block


def task_plan_from_tool_args(args: dict[str, object]) -> TaskPlan | None:
    """Parse a plan from an ``update_plan`` tool-call payload."""
    if not isinstance(args, dict):
        return None
    plan, _error = parse_task_plan(args)
    return plan


def render_plan_updated(console: Console, plan: TaskPlan | None = None) -> None:
    """Print the transcript toast: ready (nothing ran) or updated (work underway)."""
    console.print()
    if plan is not None and plan.all_pending:
        console.print(Text("Plan ready — nothing executed", style=str(ui_theme.DIM)))
        return
    console.print(Text("Plan updated", style=str(ui_theme.DIM)))


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


def task_plan_overlay_ansi(plan: TaskPlan) -> str:
    """Two-line ANSI overlay: ``Plan · n/m`` plus the current step."""
    width = prompt_line_width()
    return "\n".join(
        (
            _overlay_line(format_plan_header(plan), ui_theme.SECONDARY_ANSI, width),
            _step_overlay_line(plan.focused_step, width),
        )
    )


def render_task_plan(console: Console, plan: TaskPlan) -> None:
    """Print the toast plus the full checklist (ready dump and tests)."""
    render_plan_updated(console, plan)
    header = Text()
    header.append(format_plan_header(plan), style=str(ui_theme.SECONDARY))
    console.print(header)
    for item in plan.steps:
        # Sanitize at this sink: ``PlanStep`` may be built outside parse
        # (tests, restored payloads that skipped ``parse_task_plan``).
        glyph = PLAN_STATUS_GLYPH[item.status]
        step = strip_terminal_controls(item.step)
        line = Text()
        if item.status is PlanStepStatus.IN_PROGRESS:
            line.append(f"{glyph} ", style=f"bold {ui_theme.TEXT}")
            line.append(step, style=f"bold {ui_theme.TEXT}")
        elif item.status is PlanStepStatus.COMPLETED:
            line.append(f"{glyph} ", style=str(ui_theme.HIGHLIGHT))
            line.append(step, style=str(ui_theme.DIM))
        else:
            line.append(f"{glyph} ", style=str(ui_theme.DIM))
            line.append(step, style=str(ui_theme.DIM))
        console.print(line)
    # Keep the explanation's newlines: it is multi-line markdown (Facts,
    # hypothesis table) that render_markdown_block lays out line by line.
    explanation = strip_terminal_controls(plan.explanation, keep_whitespace=True)
    if explanation:
        render_markdown_block(console, explanation)
    console.print()


__all__ = [
    "render_plan_updated",
    "render_task_plan",
    "task_plan_from_tool_args",
    "task_plan_overlay_ansi",
]
