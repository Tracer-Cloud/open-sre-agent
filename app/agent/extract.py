"""Alert extraction — single LLM call to classify and parse a raw alert."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.incident_window import resolve_incident_window
from app.output import debug_print, get_tracker, render_investigation_header
from app.services import get_llm_for_reasoning
from app.state import InvestigationState

# Alert source values that must never be overwritten by the LLM classifier.
_CANONICAL_ALERT_SOURCES = frozenset({"openrca_dataset", "opensre", "opensre_dataset"})


class AlertExtractionInput:
    def __init__(self, raw_alert: str) -> None:
        self.raw_alert = raw_alert


try:
    from pydantic import BaseModel, Field

    class AlertDetails(BaseModel):
        is_noise: bool = Field(default=False)
        alert_name: str = Field(default="unknown")
        pipeline_name: str = Field(default="unknown")
        severity: str = Field(default="unknown")
        alert_source: str | None = Field(default=None)
        environment: str | None = Field(default=None)
        summary: str | None = Field(default=None)
        kube_namespace: str | None = Field(default=None)
        cloudwatch_log_group: str | None = Field(default=None)
        error_message: str | None = Field(default=None)
        log_query: str | None = Field(default=None)
        eks_cluster: str | None = Field(default=None)
        pod_name: str | None = Field(default=None)
        deployment: str | None = Field(default=None)

except Exception:
    pass  # Pydantic is always available; this guard is unreachable

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """Classify and extract fields from this alert message.

is_noise=true ONLY for: casual chat, greetings, trivial messages ("ok", "thanks"), or replies to existing investigation reports.
is_noise=false (default) for: any alert, error, failure, incident, warning, monitoring notification (including health checks and informational states). A payload with state=normal, a scheduled health check, or a summary saying "no errors found" is still a monitoring event and must not be treated as noise.
When in doubt, set is_noise=false.

Extract these fields from the message text:
- alert_name: The name of the alert (e.g. "Pipeline Error in Logs")
- pipeline_name: The affected pipeline/table/service name
- severity: critical/high/warning/info
- alert_source: Which platform fired this alert. If the JSON already sets alert_source to "openrca_dataset", "opensre", or "opensre_dataset", keep that exact value. Set to "grafana" if the URL/text mentions grafana.net, Grafana alerting, or grafana_folder. Set to "datadog" if it mentions datadoghq.com or Datadog monitors. Set to "honeycomb" if it mentions Honeycomb or api.honeycomb.io. Set to "coralogix" if it mentions Coralogix or DataPrime. Set to "cloudwatch" if it mentions AWS CloudWatch alarms. Set to "eks" if it mentions EKS, CrashLoopBackOff, OOMKilled, Kubernetes pods, or kube_namespace. Set to "alertmanager" if the payload contains Prometheus/Alertmanager-specific fields. Leave null if truly unknown.
- kube_namespace: Kubernetes namespace if mentioned
- cloudwatch_log_group: AWS CloudWatch log group if mentioned
- error_message: The actual error line from the alert
- log_query: The log search query from the alert body
- eks_cluster: EKS cluster name if mentioned
- pod_name: Kubernetes pod name if mentioned
- deployment: Kubernetes deployment name if mentioned

Message:
{text}
"""


def extract_alert(state: InvestigationState) -> dict[str, Any]:
    """Parse raw alert into structured state updates.

    Returns a dict of state keys (alert_name, pipeline_name, severity, etc.)
    suitable for merging into AgentState. Returns {"is_noise": True} when the
    input is classified as noise.
    """
    tracker = get_tracker()
    tracker.start("extract_alert", "Classifying and extracting alert details")

    raw_input = state.get("raw_alert")
    if raw_input is not None:
        formatted = (
            json.dumps(raw_input, indent=2, default=str)
            if isinstance(raw_input, dict)
            else str(raw_input)
        )
        logger.info("[extract_alert] Raw alert:\n%s", formatted)
        debug_print(f"Raw alert input:\n{formatted}")

    details = _extract_alert_details(state)

    if details.is_noise:
        debug_print("Message classified as noise - skipping investigation")
        tracker.complete("extract_alert", fields_updated=["is_noise"])
        _handle_noise_reaction(state)
        return {"is_noise": True}

    raw_alert = state.get("raw_alert", {})
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None

    _handle_start_reaction(state)

    debug_print(
        f"Alert: {details.alert_name} | Pipeline: {details.pipeline_name} | "
        f"Severity: {details.severity} | namespace={details.kube_namespace} | Alert ID: {alert_id}"
    )

    render_investigation_header(
        details.alert_name, details.pipeline_name, details.severity, alert_id=alert_id
    )

    enriched_alert = _enrich_raw_alert(raw_alert, details)

    tracker.complete(
        "extract_alert",
        fields_updated=["alert_name", "pipeline_name", "severity", "alert_source", "problem_md"],
    )

    result: dict[str, Any] = {
        "is_noise": False,
        "alert_name": details.alert_name,
        "pipeline_name": details.pipeline_name,
        "severity": details.severity,
        "alert_json": details.model_dump(),
        "raw_alert": enriched_alert,
        "problem_md": _make_problem_md(details),
    }
    if details.alert_source:
        result["alert_source"] = details.alert_source
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    result["incident_window"] = resolve_incident_window(raw_alert).to_dict()
    return result


def _extract_alert_details(state: InvestigationState) -> AlertDetails:
    raw_alert = state.get("raw_alert")
    if raw_alert is None:
        raise RuntimeError("raw_alert is required for alert extraction")

    text = _format_raw_alert(raw_alert)
    prompt = _EXTRACT_PROMPT.format(text=text)

    llm = get_llm_for_reasoning()
    try:
        import typing

        details = typing.cast(
            AlertDetails,
            llm.with_structured_output(AlertDetails)
            .with_config(run_name="LLM – Classify + extract alert")
            .invoke(prompt),
        )
        debug_print(
            f"Alert classified: {'NOISE' if details.is_noise else 'ALERT'} | "
            f"namespace={details.kube_namespace} | error={details.error_message}"
        )
        return details
    except Exception as err:
        debug_print(f"LLM alert extraction failed, using fallback: {err}")
        return _fallback_details(state, raw_alert)


def _format_raw_alert(raw_alert: Any) -> str:
    if isinstance(raw_alert, str):
        return raw_alert
    if isinstance(raw_alert, dict):
        if raw_alert.get("text") and not _alert_dict_needs_full_json(raw_alert):
            return str(raw_alert["text"])
        return json.dumps(raw_alert, indent=2, sort_keys=True)
    return json.dumps(raw_alert, indent=2, sort_keys=True)


def _alert_dict_needs_full_json(raw_alert: dict[str, Any]) -> bool:
    src = str(raw_alert.get("alert_source", "")).lower()
    if src in _CANONICAL_ALERT_SOURCES:
        return True
    if (
        raw_alert.get("commonLabels")
        or raw_alert.get("commonAnnotations")
        or raw_alert.get("alerts")
    ):
        return True
    for key in (
        "opensre_telemetry_relative",
        "openrca_telemetry_relative",
        "opensre_dataset_root",
        "openrca_dataset_root",
    ):
        if raw_alert.get(key):
            return True
        ann = raw_alert.get("commonAnnotations")
        if isinstance(ann, dict) and ann.get(key):
            return True
    meta = raw_alert.get("_meta")
    return bool(isinstance(meta, dict) and "openrca" in str(meta.get("purpose", "")).lower())


def _fallback_details(state: InvestigationState, raw_alert: Any) -> AlertDetails:
    alert_name = state.get("alert_name", "unknown")
    pipeline_name = state.get("pipeline_name", "unknown")
    severity = state.get("severity", "unknown")
    if isinstance(raw_alert, dict):
        labels = raw_alert.get("commonLabels") or raw_alert.get("labels", {})
        annotations = raw_alert.get("commonAnnotations") or raw_alert.get("annotations", {})
        canonical = raw_alert.get("canonical_alert")
        labels = labels if isinstance(labels, dict) else {}
        annotations = annotations if isinstance(annotations, dict) else {}
        canonical = canonical if isinstance(canonical, dict) else {}
        alert_name = (
            raw_alert.get("alert_name")
            or canonical.get("alert_name")
            or labels.get("alertname")
            or labels.get("alert_name")
            or alert_name
        )
        pipeline_name = (
            raw_alert.get("pipeline_name")
            or canonical.get("pipeline_name")
            or labels.get("pipeline_name")
            or labels.get("pipeline")
            or labels.get("service")
            or annotations.get("pipeline_name")
            or pipeline_name
        )
        severity = (
            raw_alert.get("severity")
            or canonical.get("severity")
            or labels.get("severity", severity)
        )
    return AlertDetails(
        is_noise=False,
        alert_name=alert_name or "unknown",
        pipeline_name=pipeline_name or "unknown",
        severity=severity or "unknown",
    )


def _make_problem_md(details: AlertDetails) -> str:
    parts = [
        f"# {details.alert_name}",
        f"Pipeline: {details.pipeline_name} | Severity: {details.severity}",
    ]
    if details.kube_namespace:
        parts.append(f"Namespace: {details.kube_namespace}")
    if details.error_message:
        parts.append(f"\nError: {details.error_message}")
    return "\n".join(parts)


def _enrich_raw_alert(raw_alert: Any, details: AlertDetails) -> Any:
    if not isinstance(raw_alert, dict):
        raw_alert = {}
    enriched = dict(raw_alert)
    prior_source = str(raw_alert.get("alert_source", "")).lower()
    if details.kube_namespace:
        enriched["kube_namespace"] = details.kube_namespace
    if details.cloudwatch_log_group:
        enriched["cloudwatch_log_group"] = details.cloudwatch_log_group
    if details.error_message:
        enriched["error_message"] = details.error_message
    if details.alert_source and prior_source not in _CANONICAL_ALERT_SOURCES:
        enriched["alert_source"] = details.alert_source
    if details.log_query:
        enriched["log_query"] = details.log_query
    if details.eks_cluster:
        enriched["eks_cluster"] = details.eks_cluster
    if details.pod_name:
        enriched["pod_name"] = details.pod_name
    if details.deployment:
        enriched["deployment"] = details.deployment
    return enriched


def _handle_noise_reaction(state: InvestigationState) -> None:
    slack_ctx = state.get("slack_context", {}) or {}
    _ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    if _token and _channel and _ts:
        from app.utils.slack_delivery import swap_reaction

        swap_reaction("eyes", "white_check_mark", _channel, _ts, _token)


def _handle_start_reaction(state: InvestigationState) -> None:
    slack_ctx = state.get("slack_context", {}) or {}
    _ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    if _token and _channel and _ts:
        from app.utils.slack_delivery import add_reaction

        add_reaction("eyes", _channel, _ts, _token)
