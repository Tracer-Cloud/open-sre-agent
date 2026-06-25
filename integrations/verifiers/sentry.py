"""Sentry integration verifier."""

from __future__ import annotations

from integrations.sentry import build_sentry_config, validate_sentry_config
from integrations.verification import register_validation_verifier

verify_sentry = register_validation_verifier(
    "sentry",
    build_config=build_sentry_config,
    validate_config=validate_sentry_config,
)
