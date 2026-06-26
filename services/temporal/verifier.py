"""Temporal integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from services.temporal import TemporalClient, TemporalConfig

verify_temporal = register_probe_verifier(
    "temporal",
    config=TemporalConfig.model_validate,
    client=TemporalClient,
)
