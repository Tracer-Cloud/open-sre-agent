"""OpenObserve integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.coercion import safe_int

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    base_url = str(credentials.get("base_url", "")).strip()
    api_token = str(credentials.get("api_token", "")).strip()
    username = str(credentials.get("username", "")).strip()
    password = str(credentials.get("password", "")).strip()
    if not (base_url and (api_token or (username and password))):
        return None, None
    return {
        "base_url": base_url.rstrip("/"),
        "org": str(credentials.get("org", "default")).strip() or "default",
        "api_token": api_token,
        "username": username,
        "password": password,
        "stream": str(credentials.get("stream", "")).strip(),
        "max_results": max(1, min(safe_int(credentials.get("max_results", 100), 100), 500)),
        "integration_id": record_id,
    }, "openobserve"
