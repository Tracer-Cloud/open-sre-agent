"""SMTP integration classifier."""

from __future__ import annotations

from typing import Any

from integrations.config_models import SMTPIntegrationConfig


def classify(
    credentials: dict[str, Any], _record_id: str
) -> tuple[SMTPIntegrationConfig | None, str | None]:
    try:
        cfg = SMTPIntegrationConfig.model_validate(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", 587),
                "security": credentials.get("security", "starttls"),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "from_address": credentials.get("from_address", ""),
                "default_to": credentials.get("default_to"),
            }
        )
    except Exception:
        return None, None
    return cfg, "smtp"
