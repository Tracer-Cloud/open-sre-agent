"""Honeycomb integration verifier."""

from __future__ import annotations

from integrations.config_models import HoneycombIntegrationConfig
from integrations.verification import register_probe_verifier
from vendors.honeycomb import HoneycombClient

verify_honeycomb = register_probe_verifier(
    "honeycomb",
    config=HoneycombIntegrationConfig.model_validate,
    client=HoneycombClient,
)
