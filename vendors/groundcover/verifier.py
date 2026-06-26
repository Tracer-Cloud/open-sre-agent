"""Groundcover integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from vendors.groundcover.client import GroundcoverClient, GroundcoverConfig

verify_groundcover = register_probe_verifier(
    "groundcover",
    config=GroundcoverConfig.model_validate,
    client=GroundcoverClient,
)
