"""MongoDB integration verifier."""

from __future__ import annotations

from integrations.mongodb import build_mongodb_config, validate_mongodb_config
from integrations.verification import register_validation_verifier

verify_mongodb = register_validation_verifier(
    "mongodb",
    build_config=build_mongodb_config,
    validate_config=validate_mongodb_config,
)
