"""Azure Log Analytics integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.coercion import safe_int

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    workspace_id = str(credentials.get("workspace_id", "")).strip()
    access_token = str(credentials.get("access_token", "")).strip()
    if not (workspace_id and access_token):
        return None, None
    endpoint = (
        str(credentials.get("endpoint", "https://api.loganalytics.io")).strip()
        or "https://api.loganalytics.io"
    )
    return {
        "workspace_id": workspace_id,
        "access_token": access_token,
        "endpoint": endpoint,
        "tenant_id": str(credentials.get("tenant_id", "")).strip(),
        "subscription_id": str(credentials.get("subscription_id", "")).strip(),
        "max_results": max(1, min(safe_int(credentials.get("max_results", 100), 100), 500)),
        "integration_id": record_id,
    }, "azure"
