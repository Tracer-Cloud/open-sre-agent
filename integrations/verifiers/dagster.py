"""Dagster integration verifier."""

from __future__ import annotations

from integrations.dagster import build_dagster_config, validate_dagster_config
from integrations.verification import register_validation_verifier

verify_dagster = register_validation_verifier(
    "dagster",
    build_config=build_dagster_config,
    validate_config=validate_dagster_config,
)
