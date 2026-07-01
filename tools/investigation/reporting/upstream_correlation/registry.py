"""Build an upstream-evidence provider from the current investigation state.

Vendor-neutral: iterates over builders registered in
:mod:`platform.reporting.upstream_registry` (the Datadog builder registers
itself via ``integrations.datadog.correlation.registration``). Historically
this module imported ``integrations.datadog.correlation.build_datadog_provider``
directly, which is a ``tools -> integrations`` edge (T-4 layering audit,
issue #3352, item 25).
"""

from __future__ import annotations

from typing import Any

from core.domain.types.upstream import UpstreamEvidenceProvider
from platform.reporting.upstream_registry import iter_upstream_provider_builders


def target_resource_from_state(state: dict[str, Any]) -> str:
    """Pull the correlation target resource (e.g. RDS DB identifier) from a raw alert.

    Vendor-neutral: any correlation source that needs an alert target reads
    from the same keys. Defaults to ``"unknown-rds"`` when no relevant field is
    present.
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

    Iterates over every registered builder (see
    :mod:`platform.reporting.upstream_registry`) and returns the first
    non-``None`` provider. The Datadog builder is the current default;
    additional vendors can plug in without touching this module.
    """
    _ensure_default_builders_registered()

    resolved = state.get("resolved_integrations") or {}
    if not isinstance(resolved, dict):
        return None
    target_resource = target_resource_from_state(state)
    candidate_services = candidate_services_from_state(state)

    for entry in iter_upstream_provider_builders():
        integration_config_raw = resolved.get(entry.integration_key)
        integration_config = (
            integration_config_raw if isinstance(integration_config_raw, dict) else None
        )
        provider = entry.build(
            integration_config,
            target_resource=target_resource,
            candidate_services=candidate_services,
        )
        if provider is not None:
            return provider

    return None


def _ensure_default_builders_registered() -> None:
    """Trigger side-effect registration of the built-in upstream providers.

    Kept lazy so tests that clear the registry can rebuild it via a single
    call. The import edge is concentrated here — the correlation pipeline
    body itself never imports ``integrations.*``.
    """
    # Idempotent: registration modules ``register()`` themselves at import
    # time, and subsequent imports are no-ops because of Python's module
    # cache.
    import integrations.datadog.correlation.registration  # noqa: F401
