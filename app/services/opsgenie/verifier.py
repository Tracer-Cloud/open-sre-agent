"""Opsgenie integration verifier."""

from __future__ import annotations

from app.integrations.verification import register_probe_verifier
from app.services.opsgenie import OpsGenieClient, OpsGenieConfig

verify_opsgenie = register_probe_verifier(
    "opsgenie",
    config=OpsGenieConfig.model_validate,
    client=OpsGenieClient,
)
