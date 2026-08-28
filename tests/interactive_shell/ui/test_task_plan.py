"""Task-plan overlay rendering (the plan lives only in the pinned overlay)."""

from __future__ import annotations

import re

from core.agent_harness.task_plan.plan import parse_task_plan
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.task_plan import task_plan_overlay_ansi
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


def test_all_pending_overlay_shows_the_full_indented_checklist() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm scope", "status": "pending"},
                {"step": "Verify recovery", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    overlay = _strip_ansi(task_plan_overlay_ansi(plan))
    lines = overlay.splitlines()
    assert lines[0] == "Plan ready · 0/2 executed"
    # Every step shown, indented under the header.
    assert lines[1] == "  ○ Confirm scope"
    assert lines[2] == "  ○ Verify recovery"


def test_overlay_shows_the_full_checklist_during_execution() -> None:
    # Every step stays visible with its progress glyph while work runs.
    overlay = _strip_ansi(task_plan_overlay_ansi(_sample_plan()))
    lines = overlay.splitlines()
    assert lines[0].startswith("Plan · 2/3")
    assert lines[1] == "  ✓ Capture 502 samples from checkout"
    assert lines[2] == "  ● Trace 502s to the last deploy"
    assert lines[3] == "  ○ Confirm checkout returns 2xx"


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


def test_prompt_region_keeps_the_checklist_above_invoking_tools() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    rendered = _strip_ansi(render_prompt_region(session, ReplState(), spinner).value)
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
