"""Post-parsing policy transforms for planner action results."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    split_prompt_clauses,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_with_unhandled,
)

from .constants import (
    _HTTP_INCIDENT_PASTE_RE,
    _INCIDENT_UPGRADE_SYMPTOM_RE,
    _LOCAL_LLAMA_CONNECT_RE,
    is_rich_pasted_incident,
)


@dataclass(frozen=True)
class PlannerState:
    message: str
    actions: list[PlannedAction]
    has_unhandled: bool
    session: Any | None
    trace: tuple[str, ...] = ()

    def with_update(
        self,
        *,
        actions: list[PlannedAction] | None = None,
        has_unhandled: bool | None = None,
        trace_tag: str | None = None,
    ) -> PlannerState:
        next_trace = self.trace if trace_tag is None else (*self.trace, trace_tag)
        return PlannerState(
            message=self.message,
            actions=self.actions if actions is None else actions,
            has_unhandled=self.has_unhandled if has_unhandled is None else has_unhandled,
            session=self.session,
            trace=next_trace,
        )


TransformFn = Callable[[PlannerState], PlannerState]
GuardFn = Callable[[PlannerState], bool]


@dataclass(frozen=True)
class PlannerTransform:
    name: str
    category: str
    guard: GuardFn
    apply: TransformFn


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
    state = PlannerState(
        message=message, actions=actions, has_unhandled=has_unhandled, session=None
    )
    next_state = _transform_reconcile_compound(state)
    return next_state.actions, next_state.has_unhandled


def _guard_not_final_fail_closed(state: PlannerState) -> bool:
    return not (not state.actions and state.has_unhandled)


def _guard_compound(state: PlannerState) -> bool:
    if len(split_prompt_clauses(state.message)) <= 1:
        return False
    return not (
        state.actions and all(action.kind == "assistant_handoff" for action in state.actions)
    )


def _guard_upgrade_handoff(state: PlannerState) -> bool:
    return bool(state.actions) and all(
        action.kind == "assistant_handoff" for action in state.actions
    )


def _guard_coerce_paste_handoff(state: PlannerState) -> bool:
    return bool(state.actions) and all(action.kind == "investigation" for action in state.actions)


def _guard_unconfigured_detail(state: PlannerState) -> bool:
    if state.session is None:
        return False
    return bool(getattr(state.session, "configured_integrations_known", False))


def _transform_unconfigured_detail(state: PlannerState) -> PlannerState:
    assert state.session is not None
    configured = set(getattr(state.session, "configured_integrations", ()) or ())
    lowered = state.message.lower()
    for service in ("datadog", "grafana", "sentry", "posthog", "clickhouse"):
        if (
            service in lowered
            and service not in configured
            and re.search(r"\b(show|details|verify|remove|integration)\b", lowered)
        ):
            return state.with_update(
                actions=[
                    PlannedAction(
                        kind="assistant_handoff",
                        content=f"integration_details:{service}_unconfigured",
                        position=0,
                        source="llm",
                    )
                ],
                has_unhandled=False,
                trace_tag="fail_closed_unconfigured_integration_detail",
            )
    return state


def _transform_reconcile_compound(state: PlannerState) -> PlannerState:
    det_actions, det_unhandled = map_actions_with_unhandled(state.message)
    if not det_actions or len(det_actions) <= len(state.actions):
        return state
    return state.with_update(
        actions=_as_llm_sourced(det_actions),
        has_unhandled=det_unhandled,
        trace_tag="normalize_reconcile_compound",
    )


def _transform_upgrade_handoff(state: PlannerState) -> PlannerState:
    if len(split_prompt_clauses(state.message)) != 1:
        return state
    if "?" in state.message or re.search(r"\bhow\s+(?:do|to)\b", state.message, re.IGNORECASE):
        return state
    if not _INCIDENT_UPGRADE_SYMPTOM_RE.search(state.message):
        return state

    alert_text = state.message.strip()
    return state.with_update(
        actions=[
            PlannedAction(
                kind="investigation",
                content=alert_text,
                position=0,
                source="llm",
                target_surface="investigation",
                args={"alert_text": alert_text},
            )
        ],
        has_unhandled=False,
        trace_tag="normalize_upgrade_handoff_to_incident",
    )


def _transform_coerce_paste_handoff(state: PlannerState) -> PlannerState:
    if _INCIDENT_UPGRADE_SYMPTOM_RE.search(state.message):
        return state
    if re.search(r"\bhow\s+(?:do|to)\b", state.message, re.IGNORECASE):
        return state

    is_rich_paste = is_rich_pasted_incident(state.message)
    is_http_incident = _HTTP_INCIDENT_PASTE_RE.search(state.message) is not None
    if not is_rich_paste and not is_http_incident:
        return state

    content = (
        "incident_description:rich_context"
        if is_rich_paste
        else "incident_description:http_incident"
    )
    return state.with_update(
        actions=[
            PlannedAction(
                kind="assistant_handoff",
                content=content,
                position=0,
                source="llm",
            )
        ],
        has_unhandled=False,
        trace_tag="normalize_coerce_incident_paste_handoff",
    )


POLICY_TRANSFORMS: tuple[PlannerTransform, ...] = (
    PlannerTransform(
        name="fail_closed_unconfigured_integration_detail",
        category="fail_closed_policy",
        guard=_guard_unconfigured_detail,
        apply=_transform_unconfigured_detail,
    ),
)

NORMALIZATION_TRANSFORMS: tuple[PlannerTransform, ...] = (
    PlannerTransform(
        name="normalize_reconcile_compound",
        category="normalization",
        guard=_guard_compound,
        apply=_transform_reconcile_compound,
    ),
    PlannerTransform(
        name="normalize_upgrade_handoff_to_incident",
        category="normalization",
        guard=_guard_upgrade_handoff,
        apply=_transform_upgrade_handoff,
    ),
    PlannerTransform(
        name="normalize_coerce_incident_paste_handoff",
        category="normalization",
        guard=_guard_coerce_paste_handoff,
        apply=_transform_coerce_paste_handoff,
    ),
)

FINALIZE_TRANSFORM_ORDER: tuple[PlannerTransform, ...] = (
    *POLICY_TRANSFORMS,
    *NORMALIZATION_TRANSFORMS,
)


def _apply_transforms(state: PlannerState) -> PlannerState:
    current = state
    for transform in FINALIZE_TRANSFORM_ORDER:
        if not transform.guard(current):
            continue
        current = transform.apply(current)
        if not _guard_not_final_fail_closed(current):
            break
    return current


def _finalize_planner_result_with_trace(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool, tuple[str, ...]]:
    early = _fail_closed_vague_local_model(message)
    if early is not None:
        early_actions, early_unhandled = early
        return early_actions, early_unhandled, ("fail_closed_vague_local_model",)

    state = PlannerState(
        message=message,
        actions=actions,
        has_unhandled=has_unhandled,
        session=session,
    )
    final_state = _apply_transforms(state)
    return final_state.actions, final_state.has_unhandled, final_state.trace


def _finalize_planner_result(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool]:
    final_actions, final_unhandled, _trace = _finalize_planner_result_with_trace(
        message,
        actions,
        has_unhandled,
        session=session,
    )
    return final_actions, final_unhandled
