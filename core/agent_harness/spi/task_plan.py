"""Task-plan types a host renders: the live checklist model and its parser.

A surface reads a :class:`TaskPlan` off the session to draw the ``Plan · n/m``
checklist and parses an ``update_plan`` payload through :func:`parse_task_plan`.
"""

from __future__ import annotations

from core.agent_harness.task_plan.display import (
    is_plan_diagnosis_prose,
    promote_first_pending_step,
)
from core.agent_harness.task_plan.investigation_progress import (
    advance_task_plan_for_investigation_node,
    complete_task_plan,
    investigation_phase_index,
    pipeline_phase_to_step_index,
)
from core.agent_harness.task_plan.plan import (
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    parse_task_plan,
    task_plan_to_payload,
)
from core.agent_harness.task_plan.progress import (
    PLAN_STATUS_GLYPH,
    format_plan_header,
    format_task_plan_plain,
)
from core.agent_harness.task_plan.update_plan_policy import (
    apply_update_plan_host_policy,
    apply_update_plan_session,
)
from core.agent_harness.task_plan.work_log import (
    record_task_plan_work,
    take_completed_plan_breakdown,
)

__all__ = [
    "PLAN_STATUS_GLYPH",
    "PlanStep",
    "PlanStepStatus",
    "TaskPlan",
    "advance_task_plan_for_investigation_node",
    "apply_update_plan_host_policy",
    "apply_update_plan_session",
    "complete_task_plan",
    "format_plan_header",
    "format_task_plan_plain",
    "investigation_phase_index",
    "is_plan_diagnosis_prose",
    "parse_task_plan",
    "pipeline_phase_to_step_index",
    "promote_first_pending_step",
    "record_task_plan_work",
    "take_completed_plan_breakdown",
    "task_plan_to_payload",
]
