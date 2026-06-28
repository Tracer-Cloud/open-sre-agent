"""Grafana integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import GrafanaIntegrationConfig

logger = logging.getLogger(__name__)

_GRAFANA_TOKEN_KEYS = (
    "api_key",
    "api_token",
    "read_token",
    "service_account_token",
    "bearer_token",
    "token",
    "apiKey",
    "readToken",
)


def _resolve_grafana_api_key(credentials: dict[str, Any]) -> str:
    """Return the first non-empty Grafana token field from stored credentials."""
    for key in _GRAFANA_TOKEN_KEYS:
        value = credentials.get(key)
        if isinstance(value, str) and (stripped := value.strip()):
            return stripped
    return ""


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GrafanaIntegrationConfig | None, str | None]:
    try:
        cfg = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": credentials.get("endpoint", ""),
                "api_key": _resolve_grafana_api_key(credentials),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="grafana", record_id=record_id)
        return None, None
    if not cfg.endpoint:
        return None, None
    if cfg.is_local:
        # Clear api_key for local grafana — basic auth (username/password) is used instead.
        return cfg.model_copy(update={"api_key": ""}), "grafana_local"
    if cfg.api_key and cfg.api_key != "local":
        return cfg, "grafana"
    return None, None
