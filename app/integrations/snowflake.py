"""Snowflake integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.coercion import safe_int

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    account_identifier = str(
        credentials.get("account_identifier", credentials.get("account", ""))
    ).strip()
    token = str(credentials.get("token", "")).strip()
    if not (account_identifier and token):
        return None, None
    return {
        "account_identifier": account_identifier,
        "user": str(credentials.get("user", "")).strip(),
        "password": str(credentials.get("password", "")).strip(),
        "token": token,
        "warehouse": str(credentials.get("warehouse", "")).strip(),
        "role": str(credentials.get("role", "")).strip(),
        "database": str(credentials.get("database", "")).strip(),
        "schema": str(credentials.get("schema", "")).strip(),
        "max_results": max(1, min(safe_int(credentials.get("max_results", 50), 50), 200)),
        "integration_id": record_id,
    }, "snowflake"
