"""Synthesize a structured alert from local agent-fleet SLO breaches.

``node_agent_incident`` is a sibling entry to ``node_extract_alert``: both feed the
same ``route_after_extract → resolve_integrations → …`` path. This node skips the
LLM-based extractor and builds :class:`~app.nodes.extract_alert.models.AlertDetails`
deterministically from ``context["agent_incident"]`` (see
:func:`app.state.factory.make_agent_incident_state`).
"""

from __future__ import annotations

import json
import time
from typing import Any

from langsmith import traceable

from app.alerts import normalize_alert_payload
from app.incident_window import resolve_incident_window
from app.nodes.extract_alert.models import AlertDetails
from app.output import debug_print, get_tracker, render_investigation_header
from app.state import InvestigationState
from app.types.config import NodeConfig

_AGENT_INCIDENT_SOURCE = "agent_incident"


def _extract_payload(state: InvestigationState) -> dict[str, Any] | None:
    """Return the agent-incident payload dict or ``None`` if missing/invalid."""
    ctx = state.get("context", {}) or {}
    if not isinstance(ctx, dict):
        return None
    raw = ctx.get("agent_incident")
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("agent_name", "")).strip()
    reason = str(raw.get("breach_reason", "")).strip()
    if not name or not reason:
        return None
    return raw


def _make_problem_md(details: AlertDetails, payload: dict[str, Any]) -> str:
    pid = payload.get("pid", "")
    parts = [
        f"# {details.alert_name}",
        f"Pipeline: {details.pipeline_name} | Severity: {details.severity}",
        f"Source: {_AGENT_INCIDENT_SOURCE} | Agent: {payload.get('agent_name')} (pid {pid})",
    ]
    if details.error_message:
        parts.append(f"\n**Breach:** {details.error_message}")
    tail = str(payload.get("stdout_tail") or "").strip()
    if tail:
        snippet = tail[-4000:] if len(tail) > 4000 else tail
        parts.append(f"\n## Recent output\n```\n{snippet}\n```")
    snap = payload.get("resource_snapshot")
    if isinstance(snap, dict) and snap:
        try:
            snap_txt = json.dumps(snap, indent=2, default=str)[:2000]
        except (TypeError, ValueError):
            snap_txt = str(snap)[:2000]
        parts.append(f"\n## Resource snapshot\n```json\n{snap_txt}\n```")
    return "\n".join(parts)


@traceable(name="node_agent_incident")
def node_agent_incident(
    state: InvestigationState, config: NodeConfig | None = None
) -> dict[str, Any]:
    """Build investigation state fields usually produced by ``extract_alert`` without an LLM call."""
    del config
    tracker = get_tracker()
    tracker.start("agent_incident", "Synthesizing alert from agent fleet incident")

    payload = _extract_payload(state)
    if payload is None:
        debug_print("agent_incident: missing context.agent_incident — classifying as noise")
        tracker.complete("agent_incident", fields_updated=["is_noise"])
        return {"is_noise": True}

    agent_name = str(payload.get("agent_name", "")).strip()
    breach_reason = str(payload.get("breach_reason", "")).strip()
    pid = payload.get("pid", "")
    pid_display = str(pid) if pid != "" else "-"

    alert_name = f"{agent_name} (pid {pid_display}) — agent incident"
    details = AlertDetails(
        is_noise=False,
        alert_name=alert_name,
        pipeline_name="local_agent_fleet",
        severity="warning",
        alert_source=_AGENT_INCIDENT_SOURCE,
        summary=breach_reason[:500] if breach_reason else None,
        error_message=breach_reason,
    )

    raw_alert: dict[str, Any] = {
        "alert_source": _AGENT_INCIDENT_SOURCE,
        "alert_name": alert_name,
        "severity": details.severity,
        "error_message": breach_reason,
        "annotations": {
            "summary": f"Local agent {agent_name} breached SLO: {breach_reason}",
        },
        "agent_incident": dict(payload),
    }
    raw_alert = normalize_alert_payload(raw_alert)

    debug_print(
        f"agent_incident: {alert_name} | breach={breach_reason[:120]!r}",
    )
    render_investigation_header(details.alert_name, details.pipeline_name, details.severity)

    result: dict[str, Any] = {
        "is_noise": False,
        "alert_name": details.alert_name,
        "pipeline_name": details.pipeline_name,
        "severity": details.severity,
        "alert_source": _AGENT_INCIDENT_SOURCE,
        "alert_json": details.model_dump(),
        "raw_alert": raw_alert,
        "problem_md": _make_problem_md(details, payload),
        "incident_window": resolve_incident_window(raw_alert).to_dict(),
    }
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    tracker.complete(
        "agent_incident",
        fields_updated=[
            "is_noise",
            "alert_name",
            "pipeline_name",
            "severity",
            "alert_source",
            "alert_json",
            "problem_md",
            "raw_alert",
            "incident_window",
        ],
    )
    return result
