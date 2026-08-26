"""TaskPlan parse/validate and progress counter."""

from __future__ import annotations

from core.agent_harness.task_plan.plan import (
    PlanStepStatus,
    parse_task_plan,
    task_plan_from_payload,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.progress import format_task_plan_plain


def _items(*statuses: str) -> list[dict[str, str]]:
    labels = [
        "Capture 502 samples from checkout",
        "Trace 502s to the last deploy",
        "Confirm checkout returns 2xx",
    ]
    return [{"step": labels[index], "status": status} for index, status in enumerate(statuses)]


def test_parse_rejects_a_single_step() -> None:
    plan, error = parse_task_plan({"plan": [{"step": "fix it", "status": "in_progress"}]})
    assert plan is None
    assert error is not None
    assert "at least two" in error


def test_parse_rejects_two_in_progress_steps() -> None:
    plan, error = parse_task_plan({"plan": _items("in_progress", "in_progress", "pending")})
    assert plan is None
    assert error is not None
    assert "at most one" in error


def test_parse_accepts_a_verifiable_plan() -> None:
    plan, error = parse_task_plan({"plan": _items("completed", "in_progress", "pending")})
    assert error is None
    assert plan is not None
    assert plan.current_index == 2
    assert plan.total == 3
    assert plan.steps[-1].status is PlanStepStatus.PENDING
    text = format_task_plan_plain(plan)
    assert text.startswith("Plan · 2/3")
    assert "✓" in text and "●" in text and "○" in text
    assert "(verify)" in text


def test_payload_round_trips() -> None:
    plan, error = parse_task_plan(
        {"plan": _items("pending", "pending", "pending"), "explanation": "draft"}
    )
    assert error is None and plan is not None
    assert plan.all_pending is True
    restored = task_plan_from_payload(task_plan_to_payload(plan))
    assert restored == plan
    assert format_task_plan_plain(plan).startswith("Plan ready · 0/3 executed")


def test_parse_strips_terminal_controls_from_steps_and_explanation() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "\x1b]0;pwn\x07Capture samples", "status": "in_progress"},
                {"step": "Verify\x07 recovery", "status": "pending"},
            ],
            "explanation": "why\x1b[0m now",
        }
    )
    assert error is None and plan is not None
    assert "\x1b" not in plan.steps[0].step
    assert "\x07" not in plan.steps[0].step
    assert "Capture samples" in plan.steps[0].step
    assert "\x07" not in plan.steps[1].step
    assert "\x1b" not in plan.explanation
    assert "why" in plan.explanation
