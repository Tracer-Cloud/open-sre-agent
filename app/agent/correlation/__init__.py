from __future__ import annotations

from typing import Any

from app.agent.correlation.datadog_adapter import DatadogCorrelationAdapter
from app.agent.correlation.datadog_factory import build_datadog_provider
from app.agent.correlation.datadog_provider import (
    DatadogCorrelationQueries,
    DatadogUpstreamEvidenceProvider,
)
from app.agent.correlation.providers import (
    NoopUpstreamEvidenceProvider,
    QueryBackedUpstreamEvidenceProvider,
)
from app.agent.correlation.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
    UpstreamEvidenceProvider,
)


def target_resource_from_state(state: dict[str, Any]) -> str:
    """Pull the correlation target resource (e.g. RDS DB identifier) from a raw alert.

    Vendor-neutral: any correlation source that needs an alert target
    reads from the same keys. Defaults to ``"unknown-rds"`` when no
    relevant field is present.
    """
    raw_alert = state.get("raw_alert") or {}
    if not isinstance(raw_alert, dict):
        return "unknown-rds"
    return str(
        raw_alert.get("resource")
        or raw_alert.get("resource_name")
        or raw_alert.get("db_instance")
        or raw_alert.get("db_instance_identifier")
        or "unknown-rds"
    )


def candidate_services_from_state(state: dict[str, Any]) -> tuple[str, ...]:
    """Pull upstream-service candidate names from a raw alert.

    Accepts a comma-separated string or a list/tuple under one of
    ``upstream_services`` / ``candidate_services`` / ``related_services``.
    Empty tuple when nothing relevant is present. Vendor-neutral.
    """
    raw_alert = state.get("raw_alert") or {}
    if not isinstance(raw_alert, dict):
        return ()

    raw_candidates = (
        raw_alert.get("upstream_services")
        or raw_alert.get("candidate_services")
        or raw_alert.get("related_services")
    )
    if isinstance(raw_candidates, str):
        return tuple(item.strip() for item in raw_candidates.split(",") if item.strip())
    if isinstance(raw_candidates, list | tuple):
        return tuple(str(item).strip() for item in raw_candidates if str(item).strip())
    return ()


def build_upstream_evidence_provider(state: dict[str, Any]) -> UpstreamEvidenceProvider | None:
    """Vendor-agnostic factory: pick a correlation provider for ``state``.

    Owns the agent-state shape: extracts the integration config, target
    resource, and candidate services here, then hands clean values to
    each vendor factory. Vendor factories don't know about state.

    Returns ``None`` when no integration can serve correlation evidence
    — the caller (typically :mod:`app.pipeline.pipeline`) treats that
    as "skip upstream correlation for this run".

    Adding a new correlation source is a single new factory module
    + an ``elif`` branch here. Callers must not import from
    ``app.services.<vendor>`` directly — that's a layering violation
    enforced by ``tests/pipeline/test_layering.py``.
    """
    resolved = state.get("resolved_integrations") or {}
    if not isinstance(resolved, dict):
        return None
    target_resource = target_resource_from_state(state)
    candidate_services = candidate_services_from_state(state)

    datadog_cfg_raw = resolved.get("datadog")
    datadog_provider = build_datadog_provider(
        datadog_config=datadog_cfg_raw if isinstance(datadog_cfg_raw, dict) else None,
        target_resource=target_resource,
        candidate_services=candidate_services,
    )
    if datadog_provider is not None:
        return datadog_provider

    return None


# Note: ``build_datadog_provider`` is intentionally NOT exported here.
# Callers must go through the vendor-agnostic
# :func:`build_upstream_evidence_provider`; exposing the concrete
# Datadog factory invites bypassing the abstraction. The function is
# still importable as
# ``from app.agent.correlation.datadog_factory import build_datadog_provider``
# for internal use within the correlation package.
__all__ = [
    "DatadogCorrelationAdapter",
    "DatadogCorrelationQueries",
    "DatadogUpstreamEvidenceProvider",
    "LogSignal",
    "MetricSeries",
    "NoopUpstreamEvidenceProvider",
    "QueryBackedUpstreamEvidenceProvider",
    "TopologyHint",
    "UpstreamEvidenceBundle",
    "UpstreamEvidenceProvider",
    "build_upstream_evidence_provider",
    "candidate_services_from_state",
    "target_resource_from_state",
]
