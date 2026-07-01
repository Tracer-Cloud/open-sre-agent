"""Register the Datadog upstream-evidence builder into the platform registry.

Imported as a side-effect from
:mod:`tools.investigation.reporting.upstream_correlation.registry` (via the
delivery bootstrap) so the investigation pipeline never touches
``integrations.datadog.correlation`` directly (T-4 layering audit, issue
#3352, item 25).
"""

from __future__ import annotations

from integrations.datadog.correlation.factory import build_datadog_provider
from platform.reporting.upstream_registry import register_upstream_provider_builder


def register() -> None:
    """Bind the Datadog upstream provider builder into the platform registry."""
    register_upstream_provider_builder(
        "datadog",
        integration_key="datadog",
        builder=build_datadog_provider,
    )


# Register at import time so ``import
# integrations.datadog.correlation.registration`` is a complete, idempotent
# wiring step.
register()


__all__ = ["register"]
