"""Honeycomb integration verifier."""

from __future__ import annotations

from integrations.honeycomb.client import HoneycombClient
from integrations.honeycomb.config import HoneycombIntegrationConfig
from integrations.verification import register_probe_verifier

verify_honeycomb = register_probe_verifier(
    "honeycomb",
    config=HoneycombIntegrationConfig.model_validate,
    client=HoneycombClient,
)
