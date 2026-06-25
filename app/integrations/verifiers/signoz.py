"""SigNoz integration verifier."""

from __future__ import annotations

from app.integrations.signoz import build_signoz_config, validate_signoz_config
from app.integrations.verification import register_validation_verifier

verify_signoz = register_validation_verifier(
    "signoz",
    build_config=build_signoz_config,
    validate_config=validate_signoz_config,
)
