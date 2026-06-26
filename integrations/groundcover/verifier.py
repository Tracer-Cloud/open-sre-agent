"""Groundcover integration verifier."""

from __future__ import annotations

from integrations.groundcover.client import GroundcoverClient, GroundcoverConfig
from integrations.verification import register_probe_verifier

verify_groundcover = register_probe_verifier(
    "groundcover",
    config=GroundcoverConfig.model_validate,
    client=GroundcoverClient,
)
