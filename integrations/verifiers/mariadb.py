"""MariaDB integration verifier."""

from __future__ import annotations

from integrations.mariadb import build_mariadb_config, validate_mariadb_config
from integrations.verification import register_validation_verifier

verify_mariadb = register_validation_verifier(
    "mariadb",
    build_config=build_mariadb_config,
    validate_config=validate_mariadb_config,
)
