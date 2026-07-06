"""Deterministic alert normalization helpers for extract_alert."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from core.domain.alerts.fields import (
    alert_annotations,
    alert_labels,
    alert_name_value,
    canonical_alert,
    pipeline_name_value,
    severity_value,
)

CANONICAL_ALERT_SOURCES = frozenset({"opensre", "opensre_dataset"})

RAW_ALERT_DETAIL_FIELDS = (
    "kube_namespace",
    "cloudwatch_log_group",
    "error_message",
    "log_query",
    "eks_cluster",
    "pod_name",
    "deployment",
)


class AlertDetails(BaseModel):
    """Normalized alert fields produced by the extract_alert stage."""

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


def format_raw_alert(raw_alert: Any) -> str:
    """Render raw alert payload as prompt text for the extract_alert LLM call."""
    if isinstance(raw_alert, str):
        return raw_alert
    if isinstance(raw_alert, dict):
        if raw_alert.get("text") and not needs_full_json_prompt(raw_alert):
            return str(raw_alert["text"])
        return json.dumps(raw_alert, indent=2, sort_keys=True)
    return json.dumps(raw_alert, indent=2, sort_keys=True)


def needs_full_json_prompt(raw_alert: dict[str, Any]) -> bool:
    """Return True when extract_alert should receive full JSON instead of text-only."""
    src = str(raw_alert.get("alert_source", "")).lower()
    if src in CANONICAL_ALERT_SOURCES:
        return True
    if (
        raw_alert.get("commonLabels")
        or raw_alert.get("commonAnnotations")
        or raw_alert.get("alerts")
    ):
        return True
    for key in (
        "opensre_telemetry_relative",
        "opensre_dataset_root",
    ):
        if raw_alert.get(key):
            return True
        ann = raw_alert.get("commonAnnotations")
        if isinstance(ann, dict) and ann.get(key):
            return True
    meta = raw_alert.get("_meta")
    return bool(isinstance(meta, dict) and "opensre" in str(meta.get("purpose", "")).lower())


def fallback_details(state: Mapping[str, Any], raw_alert: Any) -> AlertDetails:
    """Best-effort field extraction when the LLM path is unavailable."""
    alert_name = state.get("alert_name", "unknown")
    pipeline_name = state.get("pipeline_name", "unknown")
    severity = state.get("severity", "unknown")

    if isinstance(raw_alert, dict):
        labels = alert_labels(raw_alert)
        annotations = alert_annotations(raw_alert)
        canonical = canonical_alert(raw_alert)

        alert_name = alert_name_value(
            raw_alert,
            labels=labels,
            annotations=annotations,
            canonical=canonical,
            fallback=alert_name,
        )
        pipeline_name = pipeline_name_value(
            raw_alert,
            labels=labels,
            annotations=annotations,
            canonical=canonical,
            fallback=pipeline_name,
        )
        severity = severity_value(
            raw_alert,
            labels=labels,
            canonical=canonical,
            fallback=severity,
        )

    return AlertDetails(
        is_noise=False,
        alert_name=alert_name or "unknown",
        pipeline_name=pipeline_name or "unknown",
        severity=severity or "unknown",
    )


def make_problem_md(details: AlertDetails) -> str:
    """Build the operator-facing problem markdown header from extracted details."""
    parts = [
        f"# {details.alert_name}",
        f"Pipeline: {details.pipeline_name} | Severity: {details.severity}",
    ]
    if details.kube_namespace:
        parts.append(f"Namespace: {details.kube_namespace}")
    if details.error_message:
        parts.append(f"\nError: {details.error_message}")
    return "\n".join(parts)


def enrich_raw_alert(raw_alert: Any, details: AlertDetails) -> Any:
    """Merge extracted details back into the raw alert dict for downstream stages."""
    if not isinstance(raw_alert, dict):
        raw_alert = {}
    enriched = dict(raw_alert)
    prior_source = str(raw_alert.get("alert_source", "")).lower()

    for field_name in RAW_ALERT_DETAIL_FIELDS:
        value = getattr(details, field_name)
        if value:
            enriched[field_name] = value

    if details.alert_source and prior_source not in CANONICAL_ALERT_SOURCES:
        enriched["alert_source"] = details.alert_source
    return enriched
