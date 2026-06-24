"""Alert extraction — single LLM call to classify and parse a raw alert."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel, Field

from app.incident_window import resolve_incident_window
from app.observability import (
    debug_print,
    render_investigation_header,
)
from app.observability import (
    get_progress_tracker as get_tracker,
)
from app.services import get_llm_for_reasoning
from app.state import InvestigationState

logger = logging.getLogger(__name__)

# Alert source values that must never be overwritten by the LLM classifier.
_CANONICAL_ALERT_SOURCES = frozenset({"openrca_dataset", "opensre", "opensre_dataset"})

_EXTRACTED_STATE_FIELDS = ["alert_name", "pipeline_name", "severity", "alert_source", "problem_md"]

_RAW_ALERT_DETAIL_FIELDS = (
    "kube_namespace",
    "cloudwatch_log_group",
    "error_message",
    "log_query",
    "eks_cluster",
    "pod_name",
    "deployment",
)


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


_EXTRACT_PROMPT = """Classify and extract fields from this alert message.

is_noise=true ONLY for:
- casual chat
- greetings
- trivial messages ("ok", "thanks")
- replies to existing investigation reports

is_noise=false (default) for any alert, error, failure, incident, warning, or monitoring
notification, including health checks and informational states. A payload with state=normal,
a scheduled health check, or a summary saying "no errors found" is still a monitoring event
and must not be treated as noise.

When in doubt, set is_noise=false.

Extract these fields from the message text:
- alert_name: The name of the alert (e.g. "Pipeline Error in Logs")
- pipeline_name: The affected pipeline/table/service name
- severity: critical/high/warning/info
- alert_source: Which platform fired this alert. Preserve exact values of "openrca_dataset",
  "opensre", and "opensre_dataset" when already set in JSON. Otherwise:
  - "grafana" for grafana.net, Grafana alerting, or grafana_folder
  - "datadog" for datadoghq.com or Datadog monitors
  - "honeycomb" for Honeycomb or api.honeycomb.io
  - "coralogix" for Coralogix or DataPrime
  - "cloudwatch" for AWS CloudWatch alarms
  - "eks" for EKS, CrashLoopBackOff, OOMKilled, Kubernetes pods, or kube_namespace
  - "alertmanager" for Prometheus/Alertmanager-specific fields
  - "signoz" for SigNoz, signoz.io, or signoz_metrics
  Leave null if truly unknown.
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

    raw_alert = state.get("raw_alert")
    _log_raw_alert(raw_alert)

    details = _extract_alert_details(state)

    if details.is_noise:
        return _handle_noise(state, tracker)

    _handle_start_reaction(state)
    _render_alert_summary(details, raw_alert)

    tracker.complete("extract_alert", fields_updated=_EXTRACTED_STATE_FIELDS)
    return _build_alert_updates(state, raw_alert, details)


def _log_raw_alert(raw_alert: Any) -> None:
    if raw_alert is None:
        return
    formatted = (
        json.dumps(raw_alert, indent=2, default=str)
        if isinstance(raw_alert, dict)
        else str(raw_alert)
    )
    logger.info("[extract_alert] Raw alert:\n%s", formatted)
    debug_print(f"Raw alert input:\n{formatted}")


def _handle_noise(state: InvestigationState, tracker: Any) -> dict[str, Any]:
    debug_print("Message classified as noise - skipping investigation")
    tracker.complete("extract_alert", fields_updated=["is_noise"])
    _handle_noise_reaction(state)
    return {"is_noise": True}


def _render_alert_summary(details: AlertDetails, raw_alert: Any) -> None:
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None
    debug_print(
        f"Alert: {details.alert_name} | Pipeline: {details.pipeline_name} | "
        f"Severity: {details.severity} | namespace={details.kube_namespace} | Alert ID: {alert_id}"
    )
    render_investigation_header(
        details.alert_name, details.pipeline_name, details.severity, alert_id=alert_id
    )


def _build_alert_updates(
    state: InvestigationState,
    raw_alert: Any,
    details: AlertDetails,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "is_noise": False,
        "alert_name": details.alert_name,
        "pipeline_name": details.pipeline_name,
        "severity": details.severity,
        "alert_json": details.model_dump(),
        "raw_alert": _enrich_raw_alert(raw_alert, details),
        "problem_md": _make_problem_md(details),
        "incident_window": resolve_incident_window(raw_alert).to_dict(),
    }
    if details.alert_source:
        result["alert_source"] = details.alert_source
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    return result


def _extract_alert_details(state: InvestigationState) -> AlertDetails:
    raw_alert = state.get("raw_alert")
    if raw_alert is None:
        raise RuntimeError("raw_alert is required for alert extraction")

    text = _format_raw_alert(raw_alert)
    prompt = _EXTRACT_PROMPT.format(text=text)

    llm = get_llm_for_reasoning()
    try:
        details = cast(
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
        if raw_alert.get("text") and not _needs_full_json_prompt(raw_alert):
            return str(raw_alert["text"])
        return json.dumps(raw_alert, indent=2, sort_keys=True)
    return json.dumps(raw_alert, indent=2, sort_keys=True)


def _needs_full_json_prompt(raw_alert: dict[str, Any]) -> bool:
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
        labels = _dict_value(raw_alert, "commonLabels") or _dict_value(raw_alert, "labels")
        annotations = _dict_value(raw_alert, "commonAnnotations") or _dict_value(
            raw_alert, "annotations"
        )
        canonical = _dict_value(raw_alert, "canonical_alert")

        alert_name = _first_value(
            raw_alert.get("alert_name"),
            canonical.get("alert_name"),
            labels.get("alertname"),
            labels.get("alert_name"),
            alert_name,
        )
        pipeline_name = _first_value(
            raw_alert.get("pipeline_name"),
            canonical.get("pipeline_name"),
            labels.get("pipeline_name"),
            labels.get("pipeline"),
            labels.get("service"),
            annotations.get("pipeline_name"),
            pipeline_name,
        )
        severity = _first_value(
            raw_alert.get("severity"),
            canonical.get("severity"),
            labels.get("severity"),
            severity,
        )

    return AlertDetails(
        is_noise=False,
        alert_name=alert_name or "unknown",
        pipeline_name=pipeline_name or "unknown",
        severity=severity or "unknown",
    )


def _dict_value(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value), None)


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

    for field_name in _RAW_ALERT_DETAIL_FIELDS:
        value = getattr(details, field_name)
        if value:
            enriched[field_name] = value

    if details.alert_source and prior_source not in _CANONICAL_ALERT_SOURCES:
        enriched["alert_source"] = details.alert_source
    return enriched


def _handle_noise_reaction(state: InvestigationState) -> None:
    slack_context = _slack_reaction_context(state)
    if slack_context is None:
        return

    channel, timestamp, token = slack_context
    from app.utils.slack_delivery import swap_reaction

    swap_reaction("eyes", "white_check_mark", channel, timestamp, token)


def _handle_start_reaction(state: InvestigationState) -> None:
    slack_context = _slack_reaction_context(state)
    if slack_context is None:
        return

    channel, timestamp, token = slack_context
    from app.utils.slack_delivery import add_reaction

    add_reaction("eyes", channel, timestamp, token)


def _slack_reaction_context(state: InvestigationState) -> tuple[str, str, str] | None:
    slack_ctx = state.get("slack_context", {}) or {}
    if not isinstance(slack_ctx, dict):
        return None

    timestamp = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    channel = slack_ctx.get("channel_id")
    token = slack_ctx.get("access_token")
    if not (isinstance(channel, str) and isinstance(timestamp, str) and isinstance(token, str)):
        return None
    if not (channel and timestamp and token):
        return None
    return channel, timestamp, token
