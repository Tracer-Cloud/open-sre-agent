"""Azure SQL integration verifier."""

from __future__ import annotations

from app.integrations.azure_sql import build_azure_sql_config, validate_azure_sql_config
from app.integrations.verification import register_validation_verifier

verify_azure_sql = register_validation_verifier(
    "azure_sql",
    build_config=build_azure_sql_config,
    validate_config=validate_azure_sql_config,
)
