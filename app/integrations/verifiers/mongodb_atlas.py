"""MongoDB Atlas integration verifier."""

from __future__ import annotations

from app.integrations.mongodb_atlas import build_mongodb_atlas_config, validate_mongodb_atlas_config
from app.integrations.verification import register_validation_verifier

verify_mongodb_atlas = register_validation_verifier(
    "mongodb_atlas",
    build_config=build_mongodb_atlas_config,
    validate_config=validate_mongodb_atlas_config,
)
