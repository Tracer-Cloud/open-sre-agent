"""Post-parsing policy transforms for planner action results."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    split_prompt_clauses,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.policy_engine import (
    TransformPhase,
    apply_transform_phases,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_result,
)
from app.cli.interactive_shell.routing.policy_tags import PlannerPostprocessPolicyTag

from .constants import (
    _HTTP_INCIDENT_PASTE_RE,
    _INCIDENT_UPGRADE_SYMPTOM_RE,
    _LOCAL_LLAMA_CONNECT_RE,
    is_rich_pasted_incident,
)


class PlannerPolicyResult:
    """Finalized planner output with an explicit policy trace."""

    __slots__ = ("actions", "has_unhandled", "applied_policies")

    def __init__(
        self,
        actions: list[PlannedAction],
        has_unhandled: bool,
        applied_policies: tuple[PlannerPostprocessPolicyTag, ...],
    ) -> None:
        self.actions = actions
        self.has_unhandled = has_unhandled
        self.applied_policies = applied_policies


@dataclass(frozen=True)
class _PlannerPostprocessState:
    actions: list[PlannedAction]
    has_unhandled: bool


def _as_llm_sourced(actions: list[PlannedAction]) -> list[PlannedAction]:
    return [replace(action, source="llm") for action in actions]


def _fail_closed_vague_local_model(message: str) -> tuple[list[PlannedAction], bool] | None:
    if _LOCAL_LLAMA_CONNECT_RE.search(message):
        return [], True
    return None


def _reconcile_compound_actions(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    if len(split_prompt_clauses(message)) <= 1:
        return actions, has_unhandled
    if actions and all(action.kind == "assistant_handoff" for action in actions):
        return actions, has_unhandled

    det_result = map_actions_result(message)
    det_actions = list(det_result.actions)
    det_unhandled = det_result.has_unhandled_clause
    if not det_actions or len(det_actions) <= len(actions):
        return actions, has_unhandled
    return _as_llm_sourced(det_actions), det_unhandled


def _upgrade_handoff_to_incident(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    if len(split_prompt_clauses(message)) != 1:
        return actions, has_unhandled
    if not actions or not all(action.kind == "assistant_handoff" for action in actions):
        return actions, has_unhandled
    if "?" in message or re.search(r"\bhow\s+(?:do|to)\b", message, re.IGNORECASE):
        return actions, has_unhandled
    if not _INCIDENT_UPGRADE_SYMPTOM_RE.search(message):
        return actions, has_unhandled

    alert_text = message.strip()
    return [
        PlannedAction(
            kind="investigation",
            content=alert_text,
            position=0,
            source="llm",
            target_surface="investigation",
            args={"alert_text": alert_text},
        )
    ], False


def _fail_closed_unconfigured_integration_detail(
    message: str,
    session: Any | None,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    if session is None or not bool(getattr(session, "configured_integrations_known", False)):
        return actions, has_unhandled

    configured = set(getattr(session, "configured_integrations", ()) or ())
    lowered = message.lower()
    for service in ("datadog", "grafana", "sentry", "posthog", "clickhouse"):
        if (
            service in lowered
            and service not in configured
            and re.search(r"\b(show|details|verify|remove|integration)\b", lowered)
        ):
            return [
                PlannedAction(
                    kind="assistant_handoff",
                    content=f"integration_details:{service}_unconfigured",
                    position=0,
                    source="llm",
                )
            ], False
    return actions, has_unhandled


def _coerce_incident_paste_handoff(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    if not actions or not all(action.kind == "investigation" for action in actions):
        return actions, has_unhandled
    if _INCIDENT_UPGRADE_SYMPTOM_RE.search(message):
        return actions, has_unhandled
    if re.search(r"\bhow\s+(?:do|to)\b", message, re.IGNORECASE):
        return actions, has_unhandled

    is_rich_paste = is_rich_pasted_incident(message)
    is_http_incident = _HTTP_INCIDENT_PASTE_RE.search(message) is not None
    if not is_rich_paste and not is_http_incident:
        return actions, has_unhandled

    content = (
        "incident_description:rich_context"
        if is_rich_paste
        else "incident_description:http_incident"
    )
    return [
        PlannedAction(
            kind="assistant_handoff",
            content=content,
            position=0,
            source="llm",
        )
    ], False


def _finalize_planner_result(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool]:
    result = finalize_planner_result_with_trace(
        message,
        actions,
        has_unhandled,
        session=session,
    )
    return result.actions, result.has_unhandled


def finalize_planner_result_with_trace(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> PlannerPolicyResult:
    early = _fail_closed_vague_local_model(message)
    if early is not None:
        early_actions, early_unhandled = early
        return PlannerPolicyResult(
            early_actions,
            early_unhandled,
            (PlannerPostprocessPolicyTag.FAIL_CLOSED_VAGUE_LOCAL_MODEL,),
        )

    initial = _PlannerPostprocessState(actions=actions, has_unhandled=has_unhandled)
    phases: tuple[TransformPhase[_PlannerPostprocessState, PlannerPostprocessPolicyTag], ...] = (
        TransformPhase(
            PlannerPostprocessPolicyTag.FAIL_CLOSED_UNCONFIGURED_INTEGRATION_DETAIL,
            lambda state: _PlannerPostprocessState(
                *_fail_closed_unconfigured_integration_detail(
                    message,
                    session,
                    state.actions,
                    state.has_unhandled,
                )
            ),
        ),
        TransformPhase(
            PlannerPostprocessPolicyTag.RECONCILE_COMPOUND_WITH_DETERMINISTIC,
            lambda state: _PlannerPostprocessState(
                *_reconcile_compound_actions(message, state.actions, state.has_unhandled)
            ),
        ),
        TransformPhase(
            PlannerPostprocessPolicyTag.UPGRADE_HANDOFF_TO_INCIDENT,
            lambda state: _PlannerPostprocessState(
                *_upgrade_handoff_to_incident(message, state.actions, state.has_unhandled)
            ),
        ),
        TransformPhase(
            PlannerPostprocessPolicyTag.COERCE_INCIDENT_PASTE_HANDOFF,
            lambda state: _PlannerPostprocessState(
                *_coerce_incident_paste_handoff(message, state.actions, state.has_unhandled)
            ),
        ),
    )
    final_state, applied = apply_transform_phases(
        initial,
        phases,
        changed=lambda prev, nxt: (
            (prev.actions, prev.has_unhandled) != (nxt.actions, nxt.has_unhandled)
        ),
        stop_when=lambda state: not state.actions and state.has_unhandled,
    )
    applied_list = list(applied)
    if not final_state.actions and final_state.has_unhandled:
        applied_list.append(PlannerPostprocessPolicyTag.FAIL_CLOSED_AFTER_POLICY)
    return PlannerPolicyResult(
        final_state.actions,
        final_state.has_unhandled,
        tuple(applied_list),
    )


def _finalize_planner_result_with_trace(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool, tuple[str, ...]]:
    """Backward-compatible alias for legacy imports."""

    result = finalize_planner_result_with_trace(
        message,
        actions,
        has_unhandled,
        session=session,
    )
    compat_tags = tuple(
        {
            PlannerPostprocessPolicyTag.UPGRADE_HANDOFF_TO_INCIDENT: (
                "normalize_upgrade_handoff_to_incident"
            ),
            PlannerPostprocessPolicyTag.COERCE_INCIDENT_PASTE_HANDOFF: (
                "normalize_coerce_incident_paste_handoff"
            ),
        }.get(tag, str(tag))
        for tag in result.applied_policies
    )
    return result.actions, result.has_unhandled, compat_tags
