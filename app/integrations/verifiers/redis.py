"""Redis integration verifier."""

from __future__ import annotations

from app.integrations.redis import build_redis_config, validate_redis_config
from app.integrations.verification import register_validation_verifier

verify_redis = register_validation_verifier(
    "redis",
    build_config=build_redis_config,
    validate_config=validate_redis_config,
)
