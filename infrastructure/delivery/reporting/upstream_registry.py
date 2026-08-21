"""Upstream-evidence provider registry.

The investigation pipeline uses an :class:`UpstreamEvidenceProvider` (currently
Datadog-only) to enrich reports with correlated upstream signals. Historically
``tools/investigation/reporting/upstream_correlation/registry.py`` imported
``integrations.datadog.correlation.build_datadog_provider`` directly, which is
a ``tools -> integrations`` edge (T-4 layering audit, issue #3352, item 25).

This module inverts the dependency: each integration that wants to plug in an
upstream provider registers a builder here (a callable that consumes the
already-resolved integration config and the alert-derived knobs). The
investigation pipeline iterates over registered builders in insertion order
and returns the first non-``None`` provider.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from core.domain.types.upstream import UpstreamEvidenceProvider


class UpstreamProviderBuilder:
    """A vendor-registered factory + the resolved-integrations key it consumes."""

    __slots__ = ("integration_key", "name", "builder")

    def __init__(
        self,
        *,
        name: str,
        integration_key: str,
        builder: Callable[..., UpstreamEvidenceProvider | None],
    ) -> None:
        self.name = name
        self.integration_key = integration_key
        self.builder = builder

    def build(
        self,
        integration_config: Mapping[str, Any] | None,
        *,
        target_resource: str,
        candidate_services: tuple[str, ...],
    ) -> UpstreamEvidenceProvider | None:
        return self.builder(
            integration_config=integration_config,
            target_resource=target_resource,
            candidate_services=candidate_services,
        )


_builders: dict[str, UpstreamProviderBuilder] = {}


def register_upstream_provider_builder(
    name: str,
    *,
    integration_key: str,
    builder: Callable[..., UpstreamEvidenceProvider | None],
) -> None:
    """Register (or replace) an upstream provider builder under ``name``."""
    _builders[name] = UpstreamProviderBuilder(
        name=name,
        integration_key=integration_key,
        builder=builder,
    )


def iter_upstream_provider_builders() -> Iterable[UpstreamProviderBuilder]:
    """Return every registered builder in insertion order."""
    return tuple(_builders.values())


def clear_upstream_provider_builders() -> None:
    """Drop every registered builder (test isolation helper)."""
    _builders.clear()


__all__ = [
    "UpstreamProviderBuilder",
    "clear_upstream_provider_builders",
    "iter_upstream_provider_builders",
    "register_upstream_provider_builder",
]
