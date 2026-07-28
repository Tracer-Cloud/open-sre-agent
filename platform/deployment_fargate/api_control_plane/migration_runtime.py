"""CloudFormation custom-resource handler for the control-plane schema."""

from __future__ import annotations

# Infrastructure-related runtime code, including migrations, belongs in this package.
import os
import time
from typing import Any

from platform.deployment_fargate.api_control_plane.db.db_client import ControlPlaneDbClient

_PHYSICAL_RESOURCE_ID = "opensre-control-plane-schema"
_DATABASE_URL_SECRET_ARN_ENV = "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN"
_MIGRATION_ATTEMPTS = 3


def lambda_handler(event: dict[str, Any], _context: object) -> dict[str, str]:
    """Apply the idempotent schema on create/update and preserve it on delete."""
    if event.get("RequestType") != "Delete":
        _apply_migrations_with_retry(_database_url())
    return {"PhysicalResourceId": _PHYSICAL_RESOURCE_ID}


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    secret_arn = os.getenv(_DATABASE_URL_SECRET_ARN_ENV, "").strip()
    if not secret_arn:
        raise RuntimeError(f"DATABASE_URL or {_DATABASE_URL_SECRET_ARN_ENV} is required")
    import boto3

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    secret_string = secret.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string:
        raise RuntimeError("Database secret must contain SecretString")
    return secret_string


def _apply_migrations_with_retry(database_url: str) -> None:
    for attempt in range(_MIGRATION_ATTEMPTS):
        try:
            ControlPlaneDbClient(database_url, initialize_schema=True)
            return
        except Exception as error:
            if not _is_retryable_database_error(error) or attempt == _MIGRATION_ATTEMPTS - 1:
                raise
            time.sleep(2**attempt)


def _is_retryable_database_error(error: Exception) -> bool:
    return error.__class__.__name__ == "OperationalError" and error.__class__.__module__.startswith(
        "psycopg2"
    )
