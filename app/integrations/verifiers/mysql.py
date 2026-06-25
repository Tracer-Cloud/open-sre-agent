"""MySQL integration verifier."""

from __future__ import annotations

from app.integrations.mysql import build_mysql_config, validate_mysql_config
from app.integrations.verification import register_validation_verifier

verify_mysql = register_validation_verifier(
    "mysql",
    build_config=build_mysql_config,
    validate_config=validate_mysql_config,
)
