"""OpenSearch integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.coercion import safe_int

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    url = str(credentials.get("url", "")).strip()
    if not url:
        return None, None
    return {
        "url": url.rstrip("/"),
        "api_key": str(credentials.get("api_key", "")).strip(),
        "username": str(credentials.get("username", "")).strip(),
        "password": str(credentials.get("password", "")).strip(),
        "index_pattern": str(credentials.get("index_pattern", "*")).strip() or "*",
        "max_results": max(1, min(safe_int(credentials.get("max_results", 100), 100), 500)),
        "integration_id": record_id,
    }, "opensearch"
