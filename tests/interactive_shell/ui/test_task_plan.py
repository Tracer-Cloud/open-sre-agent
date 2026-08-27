"""Task-plan checklist rendering."""

from __future__ import annotations

import io
import re

from rich.console import Console

from core.agent_harness.task_plan.plan import parse_task_plan
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.task_plan import (
    render_plan_updated,
    render_task_plan,
    task_plan_overlay_ansi,
)
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _sample_plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Capture 502 samples from checkout", "status": "completed"},
                {"step": "Trace 502s to the last deploy", "status": "in_progress"},
                {"step": "Confirm checkout returns 2xx", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_render_task_plan_shows_counter_and_status_glyphs() -> None:
    plan = _sample_plan()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_task_plan(console, plan)
    output = buffer.getvalue()
    assert "Plan updated" in output
    assert "Plan · 2/3" in output
    assert "✓" in output
    assert "●" in output
    assert "○" in output
    assert "(verify)" not in output
    assert "Confirm checkout returns 2xx" in output


def test_all_pending_plan_says_ready_not_updated() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm scope", "status": "pending"},
                {"step": "Verify recovery", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_plan_updated(console, plan)
    output = buffer.getvalue()
    assert "Plan ready — nothing executed" in output
    assert "Plan updated" not in output
    overlay = _strip_ansi(task_plan_overlay_ansi(plan))
    assert overlay.startswith("Plan ready · 0/2 executed")
    assert "○ Confirm scope" in overlay
    assert "Verify recovery" not in overlay


def test_task_plan_overlay_shows_only_the_current_step() -> None:
    overlay = _strip_ansi(task_plan_overlay_ansi(_sample_plan()))
    assert overlay.startswith("Plan · 2/3")
    assert "● Trace 502s to the last deploy" in overlay
    assert "Capture 502 samples from checkout" not in overlay
    assert "Confirm checkout returns 2xx" not in overlay


def test_overlay_strips_control_characters_from_a_raw_step() -> None:
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    plan = TaskPlan(
        steps=(
            PlanStep(step="\x1b]0;pwn\x07Capture samples", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Verify recovery", status=PlanStepStatus.PENDING),
        )
    )
    overlay = task_plan_overlay_ansi(plan)
    assert "\x1b]" not in overlay
    assert "\x07" not in overlay
    assert "Capture samples" in overlay


def test_clip_text_strips_controls_before_measuring_width() -> None:
    from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text

    assert clip_prompt_text("\x1b" * 50 + "ok", 5) == "ok"
    clipped = clip_prompt_text("\x1b]0;pwn\x07hello", 80)
    assert "\x1b" not in clipped
    assert "\x07" not in clipped
    assert "hello" in clipped


def test_render_task_plan_strips_control_characters_from_raw_steps() -> None:
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    plan = TaskPlan(
        steps=(
            PlanStep(step="\x1b]0;pwn\x07Capture samples", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Verify recovery", status=PlanStepStatus.PENDING),
        ),
        explanation="why\x07 now",
    )
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_task_plan(console, plan)
    output = buffer.getvalue()
    assert "\x1b]" not in output
    assert "\x07" not in output
    assert "Capture samples" in output
    assert "why now" in output


def test_render_task_plan_renders_explanation_as_markdown() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Measure wait vs work", "status": "pending"},
                {"step": "Verify p99 recovery", "status": "pending"},
            ],
            "explanation": (
                "### Facts\n"
                "- p99 up, p50 flat\n\n"
                "### Hypothesis ranking\n"
                "| # | Hypothesis | Why it fits | Discriminator |\n"
                "| --- | --- | --- | --- |\n"
                "| 1 | Queue knee | peak-only | wait time up |"
            ),
        }
    )
    assert error is None and plan is not None
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    render_task_plan(console, plan)
    output = buffer.getvalue()
    assert "Hypothesis" in output
    assert "Queue knee" in output
    assert "wait time up" in output


def test_prompt_region_keeps_the_checklist_above_invoking_tools() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    rendered = _strip_ansi(render_prompt_region(session, ReplState(), spinner).value)
    assert "Plan updated" not in rendered
    assert "Plan · 2/3" in rendered
    assert "● Trace 502s to the last deploy" in rendered
    assert SpinnerState.INVOKING_TOOLS_PHASE in rendered
    assert rendered.index("Plan · 2/3") < rendered.index(SpinnerState.INVOKING_TOOLS_PHASE)
    assert "Auto (High)" in rendered
    assert rendered.index(SpinnerState.INVOKING_TOOLS_PHASE) < rendered.index("Auto (High)")


def test_idle_prompt_region_shows_ready_not_thinking() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    rendered = _strip_ansi(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "Ready" in rendered
    assert "Thinking" not in rendered
    assert SpinnerState.EXECUTING_PHASE not in rendered
    assert "Plan · 2/3" in rendered
    assert rendered.index("Ready") < rendered.index("Auto (High)")
