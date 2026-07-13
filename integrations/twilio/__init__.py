"""Twilio integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import TwilioIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[TwilioIntegrationConfig | None, str | None]:
    try:
        cfg = TwilioIntegrationConfig.model_validate(
            {
                "account_sid": credentials.get("account_sid", ""),
                "auth_token": credentials.get("auth_token", ""),
                "sms": credentials.get("sms", {}),
                "integration_id": record_id,
            }
        )
    except ValidationError:
        return None, None
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="twilio", record_id=record_id)
        return None, None
    return cfg, "twilio"
