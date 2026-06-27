"""PostgreSQL integration verifier."""

from __future__ import annotations

from integrations.postgresql import build_postgresql_config, validate_postgresql_config
from integrations.verification import register_validation_verifier

verify_postgresql = register_validation_verifier(
    "postgresql",
    build_config=build_postgresql_config,
    validate_config=validate_postgresql_config,
)
