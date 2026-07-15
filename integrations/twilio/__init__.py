"""Twilio integration classifier."""

from __future__ import annotations

from typing import Any

from integrations._classify_helpers import validate_classify
from integrations.config_models import TwilioIntegrationConfig


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[TwilioIntegrationConfig | None, str | None]:
    return validate_classify(
        TwilioIntegrationConfig,
        record_id,
        {
            "account_sid": credentials.get("account_sid", ""),
            "auth_token": credentials.get("auth_token", ""),
            "sms": credentials.get("sms", {}),
            "integration_id": record_id,
        },
        integration="twilio",
        resolved_key="twilio",
    )
