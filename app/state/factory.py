"""State constructors and defaults."""

from __future__ import annotations

import time
from typing import Any, cast

from app.alerts import normalize_alert_payload
from app.integrations.opensre.hf_remote import (
    extract_openrca_scoring_points,
    strip_scoring_points_from_alert,
)
from app.state.agent_state import AgentState, AgentStateModel
from app.state.types import ChatMessage

STATE_DEFAULTS: dict[str, Any] = {
    "mode": "chat",
    "route": "",
    "is_noise": False,
    "org_id": "",
    "user_id": "",
    "user_email": "",
    "user_name": "",
    "organization_slug": "",
    "messages": [],
    "planned_actions": [],
    "plan_rationale": "",
    "resolved_integrations": {},
    "available_sources": {},
    "available_action_names": [],
    "context": {},
    "evidence": {},
    "root_cause": "",
    "root_cause_category": "",
    "validated_claims": [],
    "non_validated_claims": [],
    "validity_score": 0.0,
    "investigation_recommendations": [],
    "remediation_steps": [],
    "investigation_loop_count": 0,
    "hypotheses": [],
    "executed_hypotheses": [],
    "evidence_entries": [],
    "masking_map": {},
    "slack_context": {},
    "discord_context": {},
    "telegram_context": {},
    "openclaw_context": {},
    "thread_id": "",
    "run_id": "",
    "_auth_token": "",
    "slack_message": "",
    "problem_md": "",
    "report": "",
}


def make_initial_state(
    raw_alert: str | dict[str, Any],
    *,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str, str] | None = None,
) -> AgentState:
    """Create initial investigation state from the raw alert payload.

    When ``investigation_metadata`` is set, it supplies ``(alert_name, pipeline_name,
    severity)`` for initial state instead of deriving them only from ``raw_alert``.
    Callers use this for HTTP/CLI overrides without mutating the alert dict.
    """
    rubric = ""
    alert_payload: str | dict[str, Any] = raw_alert
    if isinstance(alert_payload, dict):
        if opensre_evaluate:
            rubric = extract_openrca_scoring_points(alert_payload)
            if rubric:
                alert_payload = strip_scoring_points_from_alert(dict(alert_payload))
        elif extract_openrca_scoring_points(alert_payload):
            # Blind investigation: drop rubric from agent-visible alert (file may include it).
            alert_payload = strip_scoring_points_from_alert(dict(alert_payload))

        # Normalize source-specific payloads into a canonical alert shape once,
        # before any downstream extraction/planning nodes run.
        alert_payload = normalize_alert_payload(alert_payload)

    if investigation_metadata is not None:
        alert_name, pipeline_name, severity = investigation_metadata
    else:
        alert_name, pipeline_name, severity = _resolve_alert_metadata(alert_payload)

    state = AgentStateModel.model_validate(
        {
            "mode": "investigation",
            "alert_name": alert_name,
            "pipeline_name": pipeline_name,
            "severity": severity,
            "raw_alert": alert_payload,
            "investigation_started_at": time.monotonic(),
            "opensre_evaluate": opensre_evaluate,
            "opensre_eval_rubric": rubric,
            **{k: v for k, v in STATE_DEFAULTS.items() if k not in ("mode", "messages")},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))


def _resolve_alert_metadata(raw_alert: str | dict[str, Any]) -> tuple[str, str, str]:
    """Best-effort defaults used until ``extract_alert`` does deeper parsing."""
    if not isinstance(raw_alert, dict):
        return ("Incident", "unknown", "warning")

    labels = _dict_value(raw_alert, "commonLabels") or _dict_value(raw_alert, "labels")
    annotations = _dict_value(raw_alert, "commonAnnotations") or _dict_value(
        raw_alert, "annotations"
    )
    canonical = _dict_value(raw_alert, "canonical_alert")

    alert_name = _first_text(
        raw_alert.get("alert_name"),
        raw_alert.get("title"),
        canonical.get("alert_name"),
        labels.get("alertname"),
        labels.get("alert_name"),
        annotations.get("summary"),
        "Incident",
    )
    pipeline_name = _first_text(
        raw_alert.get("pipeline_name"),
        canonical.get("pipeline_name"),
        labels.get("pipeline_name"),
        labels.get("pipeline"),
        labels.get("service"),
        raw_alert.get("service"),
        "unknown",
    )
    severity = _first_text(
        raw_alert.get("severity"),
        canonical.get("severity"),
        labels.get("severity"),
        labels.get("priority"),
        "warning",
    )
    return alert_name, pipeline_name, severity


def _dict_value(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def make_agent_incident_state(
    *,
    agent_name: str,
    breach_reason: str,
    pid: str | int = "",
    stdout_tail: str = "",
    resource_snapshot: dict[str, Any] | None = None,
    opensre_evaluate: bool = False,
) -> AgentState:
    """Create initial state for :func:`node_agent_incident` (local agent fleet SLO breach).

    The synthesizer reads ``context["agent_incident"]``. Callers should populate it
    with at least ``agent_name`` and ``breach_reason``.
    """
    payload: dict[str, Any] = {
        "agent_name": str(agent_name).strip(),
        "breach_reason": str(breach_reason).strip(),
        "pid": pid,
        "stdout_tail": str(stdout_tail or ""),
        "resource_snapshot": dict(resource_snapshot or {}),
    }
    state = AgentStateModel.model_validate(
        {
            "mode": "agent_incident",
            "raw_alert": {},
            "context": {"agent_incident": payload},
            "investigation_started_at": time.monotonic(),
            "opensre_evaluate": opensre_evaluate,
            **{k: v for k, v in STATE_DEFAULTS.items() if k not in ("mode", "messages", "context")},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))


def make_chat_state(
    org_id: str = "",
    user_id: str = "",
    user_email: str = "",
    user_name: str = "",
    organization_slug: str = "",
    messages: list[ChatMessage] | None = None,
) -> AgentState:
    """Create initial state for chat mode."""
    state = AgentStateModel.model_validate(
        {
            "mode": "chat",
            "org_id": org_id,
            "user_id": user_id,
            "user_email": user_email,
            "user_name": user_name,
            "organization_slug": organization_slug,
            "messages": messages or [],
            "context": {},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))
