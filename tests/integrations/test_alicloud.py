"""Regression test for Sentry issue PYTHON-2G.

EffectiveIntegrations was missing the ``alicloud`` field, causing a
ValidationError when a user had an alicloud integration in their store.
"""

from __future__ import annotations

from typing import Any


def test_effective_integrations_field_exists() -> None:
    """``alicloud`` must be a declared field on EffectiveIntegrations."""
    from app.integrations.effective_models import (
        EffectiveIntegrationEntry,
        EffectiveIntegrations,
    )

    entry = EffectiveIntegrationEntry(
        source="local env",
        config={"credentials": {}, "integration_id": "env-alicloud"},
    )
    effective: dict[str, Any] = {"alicloud": entry.model_dump()}
    # Should not raise (EffectiveIntegrations uses extra='forbid').
    result = EffectiveIntegrations.model_validate(effective)
    assert result.alicloud is not None


def test_registry_spec_present() -> None:
    """alicloud must be registered with direct_effective=True."""
    from app.integrations.registry import DIRECT_CLASSIFIED_EFFECTIVE_SERVICES, INTEGRATION_SPECS

    spec = next((s for s in INTEGRATION_SPECS if s.service == "alicloud"), None)
    assert spec is not None
    assert spec.direct_effective is True
    assert "alicloud" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
