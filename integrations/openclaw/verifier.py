"""OpenClaw integration verifier."""

from __future__ import annotations

from integrations.openclaw import build_openclaw_config, validate_openclaw_config
from integrations.verification import register_validation_verifier

verify_openclaw = register_validation_verifier(
    "openclaw",
    build_config=build_openclaw_config,
    validate_config=validate_openclaw_config,
)
