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


def build_upstream_evidence_provider(state: dict[str, Any]) -> UpstreamEvidenceProvider | None:
    """Vendor-agnostic factory: pick a correlation provider for ``state``.

    Inspects the agent state's ``resolved_integrations`` and delegates to
    the matching vendor factory. Returns ``None`` when no integration
    can serve correlation evidence — the caller treats that as "skip
    upstream correlation for this run".

    Adding a new correlation source is a single new factory module
    + an ``elif`` branch here. Callers (specifically
    :mod:`app.pipeline.pipeline`) must not import from
    ``app.services.<vendor>`` directly — that's a layering violation
    enforced by ``tests/pipeline/test_layering.py``.
    """
    provider = build_datadog_provider(state)
    if provider is not None:
        return provider
    return None


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
    "build_datadog_provider",
    "build_upstream_evidence_provider",
]
