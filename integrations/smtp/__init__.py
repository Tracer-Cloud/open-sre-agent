"""SMTP integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import SMTPIntegrationConfig

logger = logging.getLogger(__name__)


def _smtp_validation_failure() -> ValueError:
    """Return a non-secret exception for SMTP config validation failures."""
    return ValueError("SMTPIntegrationConfig validation failed")


def classify(
    credentials: dict[str, Any], record_id: str
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
    except ValidationError:
        report_classify_failure(
            _smtp_validation_failure(),
            logger=logger,
            integration="smtp",
            record_id=record_id,
        )
        return None, None
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="smtp", record_id=record_id)
        return None, None
    return cfg, "smtp"
