"""Opsgenie integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from services.opsgenie import OpsGenieClient, OpsGenieConfig

verify_opsgenie = register_probe_verifier(
    "opsgenie",
    config=OpsGenieConfig.model_validate,
    client=OpsGenieClient,
)
