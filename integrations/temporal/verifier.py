"""Temporal integration verifier."""

from __future__ import annotations

from integrations.temporal.client import TemporalClient, TemporalConfig
from integrations.verification import register_probe_verifier

verify_temporal = register_probe_verifier(
    "temporal",
    config=TemporalConfig.model_validate,
    client=TemporalClient,
)
