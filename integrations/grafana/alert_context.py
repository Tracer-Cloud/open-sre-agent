"""Grafana alert context used to scope deterministic Loki seed queries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from config.constants import INVESTIGATION_CONTEXT_SOURCE_KEY

_SERVICE_NAME_KEYS = ("service_name", "service")
_PIPELINE_NAME_KEYS = ("pipeline_name", "pipeline")
_LOG_QUERY_KEYS = ("log_query", "loki_query", "logql")
_DATASOURCE_TYPE_KEYS = ("datasource_type", "datasource")


@dataclass(frozen=True, slots=True)
class GrafanaLokiAlertContext:
    """Alert-derived values that make a Loki query incident-specific."""

    service_name: str = ""
    pipeline_name: str = ""
    log_query: str = ""
    datasource_uid: str = ""


def resolve_grafana_loki_alert_context(
    sources: Mapping[str, Any],
) -> GrafanaLokiAlertContext:
    """Resolve Loki scope from normalized and raw Grafana alert payloads.

    Raw alert fields take precedence because they are deterministic source data.
    ``alert_json`` is a fallback for values extracted from unstructured alerts.
    """
    investigation_context = sources.get(INVESTIGATION_CONTEXT_SOURCE_KEY)
    if not isinstance(investigation_context, Mapping):
        return GrafanaLokiAlertContext()

    blocks = [
        *_payload_blocks(investigation_context.get("raw_alert")),
        *_payload_blocks(investigation_context.get("alert_json")),
    ]
    log_query = _first_text(blocks, _LOG_QUERY_KEYS)
    return GrafanaLokiAlertContext(
        service_name=_first_text(blocks, _SERVICE_NAME_KEYS),
        pipeline_name=_first_text(blocks, _PIPELINE_NAME_KEYS),
        log_query=log_query,
        datasource_uid=_resolve_loki_datasource_uid(blocks, log_query=log_query),
    )


def _payload_blocks(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []

    blocks: list[Mapping[str, Any]] = [value]
    for key in (
        "canonical_alert",
        "commonLabels",
        "labels",
        "commonAnnotations",
        "annotations",
    ):
        block = value.get(key)
        if isinstance(block, Mapping):
            blocks.append(block)

    alerts = value.get("alerts")
    if isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, Mapping):
                continue
            blocks.append(alert)
            for key in ("labels", "annotations"):
                block = alert.get(key)
                if isinstance(block, Mapping):
                    blocks.append(block)
    return blocks


def _first_text(
    blocks: list[Mapping[str, Any]],
    keys: tuple[str, ...],
) -> str:
    for block in blocks:
        for key in keys:
            value = block.get(key)
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            text = str(value).strip()
            if text:
                return text
    return ""


def _resolve_loki_datasource_uid(
    blocks: list[Mapping[str, Any]],
    *,
    log_query: str,
) -> str:
    explicit_loki_uid = _first_text(blocks, ("loki_datasource_uid",))
    if explicit_loki_uid:
        return explicit_loki_uid

    datasource_type = _first_text(blocks, _DATASOURCE_TYPE_KEYS).lower()
    if log_query or datasource_type == "loki":
        return _first_text(blocks, ("datasource_uid",))
    return ""


__all__ = ["GrafanaLokiAlertContext", "resolve_grafana_loki_alert_context"]
