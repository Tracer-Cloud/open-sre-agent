"""Honeycomb integration verifier."""

from __future__ import annotations

from app.integrations.config_models import HoneycombIntegrationConfig
from app.integrations.verification import register_probe_verifier
from app.services.honeycomb import HoneycombClient

verify_honeycomb = register_probe_verifier(
    "honeycomb",
    config=HoneycombIntegrationConfig.model_validate,
    client=HoneycombClient,
)
