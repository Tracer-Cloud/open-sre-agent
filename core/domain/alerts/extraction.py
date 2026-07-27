"""Deterministic alert normalization helpers for extract_alert."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from core.domain.alerts.fields import (
    alert_annotations,
    alert_labels,
    alert_name_value,
    canonical_alert,
    severity_value,
)

CANONICAL_ALERT_SOURCES = frozenset({"opensre", "opensre_dataset"})

# Fields every alert source can populate; vendor-owned optional fields (e.g.
# Kubernetes' kube_namespace, AWS's cloudwatch_log_group) are registered by
# integrations/<vendor>/ via register_alert_detail_fields.
_CORE_ALERT_DETAIL_FIELDS: tuple[str, ...] = ("error_message", "log_query")

_registered_alert_detail_fields: list[str] = []


def register_alert_detail_fields(*names: str) -> None:
    """Register vendor-owned optional ``AlertDetails`` field names."""
    for name in names:
        if name not in _registered_alert_detail_fields:
            _registered_alert_detail_fields.append(name)


def clear_alert_detail_fields() -> None:
    """Remove all registered vendor alert detail field names (tests)."""
    _registered_alert_detail_fields.clear()


def alert_detail_field_names() -> tuple[str, ...]:
    """Return every optional detail field name: core generics + registered vendor fields."""
    return (*_CORE_ALERT_DETAIL_FIELDS, *_registered_alert_detail_fields)


class AlertDetails(BaseModel):
    """Normalized alert fields produced by the extract_alert stage.

    Vendor-owned optional fields (``kube_namespace``, ``cloudwatch_log_group``,
    etc.) are not declared here — they are registered by ``integrations/<vendor>/``
    via :func:`register_alert_detail_fields` and accepted through
    ``extra="allow"`` so this model stays constructible from vendor kwargs
    (``AlertDetails(kube_namespace=...)``) and introspectable via
    ``getattr(details, name, None)`` without core hardcoding vendor field names.
    Use :func:`build_alert_details_model` where the LLM's output schema should
    declare the registered vendor fields explicitly (structured-output calls).
    """

    model_config = ConfigDict(extra="allow")

    is_noise: bool = Field(default=False)
    alert_name: str = Field(default="unknown")
    severity: str = Field(default="unknown")
    alert_source: str | None = Field(default=None)
    environment: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    log_query: str | None = Field(default=None)


def build_alert_details_model() -> type[AlertDetails]:
    """Return an ``AlertDetails`` subclass with registered vendor fields declared.

    Structured-output LLM calls need the vendor fields declared (not just
    allowed as extras) so the generated JSON schema instructs the model to
    extract them. Falls back to ``AlertDetails`` itself when no vendor fields
    are registered.
    """
    if not _registered_alert_detail_fields:
        return AlertDetails
    vendor_fields: dict[str, Any] = {
        name: (str | None, Field(default=None)) for name in _registered_alert_detail_fields
    }
    return create_model(
        "AlertDetailsWithVendorFields",
        __base__=AlertDetails,
        **vendor_fields,
    )


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
        severity = severity_value(
            raw_alert,
            labels=labels,
            canonical=canonical,
            fallback=severity,
        )

    return AlertDetails(
        is_noise=False,
        alert_name=alert_name or "unknown",
        severity=severity or "unknown",
    )


def make_problem_md(details: AlertDetails) -> str:
    """Build the operator-facing problem markdown header from extracted details."""
    parts = [
        f"# {details.alert_name}",
        f"Severity: {details.severity}",
    ]
    namespace = getattr(details, "kube_namespace", None)
    if namespace:
        parts.append(f"Namespace: {namespace}")
    if details.error_message:
        parts.append(f"\nError: {details.error_message}")
    return "\n".join(parts)


def enrich_raw_alert(raw_alert: Any, details: AlertDetails) -> Any:
    """Merge extracted details back into the raw alert dict for downstream stages."""
    if not isinstance(raw_alert, dict):
        raw_alert = {}
    enriched = dict(raw_alert)
    prior_source = str(raw_alert.get("alert_source", "")).lower()

    for field_name in alert_detail_field_names():
        value = getattr(details, field_name, None)
        if value:
            enriched[field_name] = value

    if details.alert_name and details.alert_name != "unknown":
        enriched["alert_name"] = details.alert_name

    canonical = enriched.get("canonical_alert")
    if isinstance(canonical, dict):
        updated_canonical = dict(canonical)
        if details.alert_name and details.alert_name != "unknown":
            updated_canonical["alert_name"] = details.alert_name
        enriched["canonical_alert"] = updated_canonical

    if details.alert_source and prior_source not in CANONICAL_ALERT_SOURCES:
        enriched["alert_source"] = details.alert_source
    return enriched
