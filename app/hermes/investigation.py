"""Optional bridge from Hermes incidents into the OpenSRE investigation pipeline.

The :func:`run_incident_investigation` helper turns a :class:`HermesIncident`
into a Grafana-shaped alert payload, calls
:func:`app.pipeline.runners.run_investigation`, and extracts a
human-readable summary from the resulting :class:`AgentState`.

The investigation pipeline is heavy (LangGraph, LLM calls, integration
resolution) so this module imports it lazily — callers that never invoke
the bridge pay no import cost.
"""

from __future__ import annotations

from typing import Any

from app.hermes.incident import HermesIncident, LogRecord

# Trim long evidence blobs so the alert annotations stay well under any
# downstream payload limit (Grafana webhook bodies, LLM prompts, etc.).
_MAX_ANNOTATION_LINES = 6
_MAX_ANNOTATION_LINE_CHARS = 240


def build_alert_from_incident(incident: HermesIncident) -> dict[str, Any]:
    """Build a Grafana-shaped alert payload from a Hermes incident.

    The payload shape mirrors what
    :func:`tests.utils.alert_factory.factory.create_alert` produces, so
    it lands in the investigation pipeline through the same code path as
    a real Grafana webhook. Severity ``MEDIUM`` is normalized to
    ``"warning"`` because the investigation graph treats severity as a
    free-form string aligned with Grafana conventions.
    """
    severity_label = _severity_label(incident.severity.value)
    pipeline_name = incident.logger or "hermes"
    return {
        "alert_name": f"Hermes incident: {incident.title}",
        "pipeline_name": pipeline_name,
        "severity": severity_label,
        "alert_source": "hermes",
        "message": incident.title,
        "raw_alert": incident.title,
        "commonLabels": {
            "alertname": "HermesIncident",
            "severity": severity_label,
            "pipeline_name": pipeline_name,
            "rule": incident.rule,
        },
        "commonAnnotations": {
            "summary": incident.title,
            "rule": incident.rule,
            "fingerprint": incident.fingerprint,
            "logger": incident.logger,
            "detected_at": incident.detected_at.isoformat(),
            "evidence": _format_records(incident.records),
            "context_sources": "hermes_logs",
            **({"run_id": incident.run_id} if incident.run_id else {}),
        },
    }


def run_incident_investigation(incident: HermesIncident) -> str | None:
    """Invoke the OpenSRE investigation pipeline for ``incident``.

    Returns the resulting summary string, or ``None`` if the pipeline ran
    successfully but produced no usable output. If :func:`run_investigation`
    raises, the exception propagates to the caller — :class:`TelegramSink`
    maps that to an operator-visible "attempted (failed)" marker. Heavy
    imports are deferred so the Hermes sink can be imported in environments
    where LangGraph and friends are not installed (e.g. unit tests).
    """
    # Imported lazily — pulling app.pipeline.runners at module import
    # time would force every Hermes consumer to pay the LangGraph import
    # cost even when no investigation ever runs.
    from app.pipeline.runners import run_investigation

    alert = build_alert_from_incident(incident)
    state = run_investigation(
        alert_name=str(alert["alert_name"]),
        pipeline_name=str(alert["pipeline_name"]),
        severity=str(alert["severity"]),
        raw_alert=alert,
    )
    return _extract_summary(state)


def _extract_summary(state: Any) -> str | None:
    """Pull the best human-readable summary from an investigation state.

    The investigation graph exposes several overlapping summary fields
    (``summary``, ``root_cause``, ``problem_md``) depending on which
    nodes ran. We pick the first non-empty one in priority order.
    """
    if state is None:
        return None
    if not isinstance(state, dict):  # AgentState is a TypedDict but graphs sometimes return models
        state = getattr(state, "__dict__", state)
        if not isinstance(state, dict):
            return None
    for key in ("summary", "root_cause", "problem_md", "report"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _severity_label(value: str) -> str:
    """Map :class:`IncidentSeverity` values to Grafana severity strings."""
    normalized = value.strip().lower()
    if normalized in {"critical", "high"}:
        return "critical"
    if normalized == "medium":
        return "warning"
    return normalized or "warning"


def _format_records(records: tuple[LogRecord, ...]) -> str:
    if not records:
        return ""
    lines = []
    for record in records[:_MAX_ANNOTATION_LINES]:
        raw = record.raw
        if len(raw) > _MAX_ANNOTATION_LINE_CHARS:
            raw = raw[: _MAX_ANNOTATION_LINE_CHARS - 1] + "…"
        lines.append(raw)
    omitted = len(records) - len(lines)
    if omitted > 0:
        lines.append(f"… ({omitted} more record{'s' if omitted != 1 else ''} omitted)")
    return "\n".join(lines)


__all__ = [
    "build_alert_from_incident",
    "run_incident_investigation",
]
