"""WhatsApp ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.whatsapp`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _WhatsAppReportDeliveryAdapter:
    """WhatsApp delivery adapter — messages a single ``to`` number via Twilio."""

    name = "whatsapp"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        whatsapp_creds = resolved.get("whatsapp") if isinstance(resolved, dict) else None
        if not whatsapp_creds:
            logger.debug("[publish] whatsapp delivery: no whatsapp integration configured")
            return False

        whatsapp_ctx: dict[str, Any] = state.get("whatsapp_context") or {}
        account_sid = whatsapp_ctx.get("account_sid") or whatsapp_creds.get("account_sid", "")
        auth_token = whatsapp_ctx.get("auth_token") or whatsapp_creds.get("auth_token", "")
        from_number = whatsapp_ctx.get("from_number") or whatsapp_creds.get("from_number", "")
        to = whatsapp_ctx.get("to") or whatsapp_creds.get("default_to", "")
        logger.debug(
            "[publish] whatsapp delivery: to=%s account_sid=%s auth_configured=%s from=%s",
            to,
            account_sid,
            bool(auth_token),
            from_number,
        )
        if not (account_sid and auth_token and from_number and to):
            logger.debug(
                "[publish] whatsapp delivery: skipped - account_sid_present=%s "
                "auth_token_present=%s from_number_present=%s to_present=%s",
                bool(account_sid),
                bool(auth_token),
                bool(from_number),
                bool(to),
            )
            return False

        from integrations.whatsapp.delivery import send_whatsapp_report

        posted, error = send_whatsapp_report(
            messages.get("whatsapp_text", ""),
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": from_number,
                "to": to,
            },
        )
        logger.debug("[publish] whatsapp delivery: posted=%s error=%s", posted, error)
        if not posted:
            logger.warning(
                "[publish] WhatsApp delivery failed: to=%s error=%s",
                to,
                error,
            )
        return True


whatsapp_delivery_adapter = _WhatsAppReportDeliveryAdapter()
register_delivery_adapter(whatsapp_delivery_adapter)

__all__ = ["whatsapp_delivery_adapter"]
