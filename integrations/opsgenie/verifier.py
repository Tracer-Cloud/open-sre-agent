"""Opsgenie integration verifier."""

from __future__ import annotations

from integrations.opsgenie.client import OpsGenieClient, OpsGenieConfig
from integrations.verification import register_probe_verifier

verify_opsgenie = register_probe_verifier(
    "opsgenie",
    config=OpsGenieConfig.model_validate,
    client=OpsGenieClient,
)
