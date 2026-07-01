"""Twilio SMS ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.twilio`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _TwilioSmsReportDeliveryAdapter:
    """Twilio SMS delivery adapter — sends when the SMS channel is explicitly enabled."""

    name = "twilio"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        twilio_creds = resolved.get("twilio") if isinstance(resolved, dict) else None
        if not twilio_creds:
            logger.debug("[publish] twilio delivery: no twilio integration configured")
            return False

        sms_cfg = twilio_creds.get("sms") or {}
        if not sms_cfg.get("enabled"):
            return False

        twilio_sms_ctx: dict[str, Any] = state.get("twilio_sms_context") or {}
        sms_to = twilio_sms_ctx.get("to") or sms_cfg.get("default_to") or ""
        sms_from = sms_cfg.get("from_number", "")
        messaging_service_sid = sms_cfg.get("messaging_service_sid", "")
        account_sid = twilio_creds.get("account_sid", "")
        auth_token = twilio_creds.get("auth_token", "")
        logger.debug(
            "[publish] twilio sms delivery: to=%s from=%s msg_service=%s account_sid_present=%s",
            sms_to,
            sms_from,
            messaging_service_sid,
            bool(account_sid),
        )

        deliverable = account_sid and auth_token and sms_to and (sms_from or messaging_service_sid)
        if not deliverable:
            logger.warning(
                "[publish] twilio sms delivery: skipped - SMS channel is enabled "
                "but not deliverable (recipient_present=%s sender_present=%s "
                "account_sid_present=%s auth_token_present=%s). "
                "Set TWILIO_SMS_DEFAULT_TO to enable auto-delivery.",
                bool(sms_to),
                bool(sms_from or messaging_service_sid),
                bool(account_sid),
                bool(auth_token),
            )
            return False

        from integrations.twilio.delivery import send_twilio_sms_report

        ok, error, sid = send_twilio_sms_report(
            messages.get("sms_text", ""),
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": sms_from,
                "messaging_service_sid": messaging_service_sid,
                "to": sms_to,
            },
        )
        logger.debug(
            "[publish] twilio sms delivery: posted=%s sid=%s error=%s",
            ok,
            sid,
            error,
        )
        if not ok:
            logger.warning(
                "[publish] Twilio SMS delivery failed: to=%s error=%s",
                sms_to,
                error,
            )
        return True


twilio_delivery_adapter = _TwilioSmsReportDeliveryAdapter()
register_delivery_adapter(twilio_delivery_adapter)

__all__ = ["twilio_delivery_adapter"]
