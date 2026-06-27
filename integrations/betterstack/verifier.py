"""Better Stack integration verifier."""

from __future__ import annotations

from integrations.betterstack import build_betterstack_config, validate_betterstack_config
from integrations.verification import register_validation_verifier

verify_betterstack = register_validation_verifier(
    "betterstack",
    build_config=build_betterstack_config,
    validate_config=validate_betterstack_config,
)
