from __future__ import annotations

import logging
from typing import Any, Protocol, cast

from app.cli.support.output import get_tracker
from app.correlation.providers import NoopUpstreamEvidenceProvider
from app.correlation.runtime import build_runtime_correlation
from app.correlation.upstream import UpstreamEvidenceBundle
from app.state import InvestigationState
from app.utils.tracing import traceable

logger = logging.getLogger(__name__)


class _UpstreamEvidenceProvider(Protocol):
    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        """Collect upstream evidence for the current incident window."""
        raise NotImplementedError


def _empty_correlation() -> dict[str, list[dict[str, Any]]]:
    return {
        "correlated_signals": [],
        "most_likely_causal_drivers": [],
    }


def _extract_configurable(config: Any | None) -> dict[str, Any]:
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            return configurable
    return {}


def _provider_from_config(config: Any | None) -> _UpstreamEvidenceProvider:
    configurable = _extract_configurable(config)
    provider = configurable.get("upstream_evidence_provider")
    if provider is not None and hasattr(provider, "collect_upstream_evidence"):
        return cast(_UpstreamEvidenceProvider, provider)
    return NoopUpstreamEvidenceProvider()


def _incident_window(state: InvestigationState) -> tuple[str, str]:
    window = state.get("incident_window") or {}
    if isinstance(window, dict):
        since = window.get("since")
        until = window.get("until")
        if isinstance(since, str) and isinstance(until, str):
            return since, until
    raise ValueError("incident_window is missing or malformed")


def _raw_alert_dict(state: InvestigationState) -> dict[str, Any]:
    raw_alert = state.get("raw_alert") or {}
    return raw_alert if isinstance(raw_alert, dict) else {}


@traceable(name="node_correlate_upstream")
def node_correlate_upstream(
    state: InvestigationState,
    config: Any | None = None,
) -> dict[str, Any]:
    """Attach upstream-correlation payload to investigation state."""
    tracker = get_tracker()
    tracker.start("correlate_upstream")

    existing = state.get("correlation")
    if isinstance(existing, dict):
        correlated_signals = existing.get("correlated_signals")
        causal_drivers = existing.get("most_likely_causal_drivers")

        if isinstance(correlated_signals, list) and isinstance(causal_drivers, list):
            tracker.complete("correlate_upstream")
            return {"correlation": existing}

    raw_alert = _raw_alert_dict(state)
    service_name = str(
        raw_alert.get("service")
        or raw_alert.get("service_name")
        or state.get("pipeline_name")
        or "unknown"
    )
    alert_id = str(raw_alert.get("id") or raw_alert.get("alert_id") or "unknown")
    try:
        window_start, window_end = _incident_window(state)
    except ValueError:
        tracker.complete("correlate_upstream")
        return {"correlation": _empty_correlation()}

    provider = _provider_from_config(config)

    try:
        evidence = provider.collect_upstream_evidence(
            alert_id=alert_id,
            service_name=service_name,
            window_start=window_start,
            window_end=window_end,
        )

        target_resource = str(
            raw_alert.get("resource")
            or raw_alert.get("resource_name")
            or raw_alert.get("db_instance")
            or raw_alert.get("db_instance_identifier")
            or "unknown-rds"
        )

        correlation = build_runtime_correlation(
            evidence,
            target_resource=target_resource,
        )
    except Exception:
        logger.warning(
            "Failed to build upstream correlation payload",
            exc_info=True,
        )
        tracker.complete("correlate_upstream")
        return {"correlation": _empty_correlation()}

    tracker.complete("correlate_upstream")

    return {"correlation": correlation}
