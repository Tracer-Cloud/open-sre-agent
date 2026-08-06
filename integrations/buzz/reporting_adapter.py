"""Buzz ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.buzz`` directly (same layering rule as the other vendor
adapters — T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _BuzzReportDeliveryAdapter:
    """Buzz delivery adapter — posts to a channel when credentials are set."""

    name = "buzz"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        buzz_creds = resolved.get("buzz") if isinstance(resolved, dict) else None
        if not buzz_creds:
            logger.debug("[publish] buzz delivery: no buzz integration configured")
            return False

        from core.state.channel_context import get_channel_context

        buzz_ctx = get_channel_context(state, "buzz")
        relay_url = buzz_ctx.get("relay_url") or buzz_creds.get("relay_url", "")
        private_key = buzz_ctx.get("private_key") or buzz_creds.get("private_key", "")
        auth_tag = buzz_ctx.get("auth_tag") or buzz_creds.get("auth_tag", "")
        buzz_path = buzz_ctx.get("buzz_path") or buzz_creds.get("buzz_path", "")
        channel = buzz_ctx.get("channel") or buzz_creds.get("default_channel", "")
        ready = bool(private_key and channel)
        logger.debug(
            "[publish] buzz delivery: relay_url=%s channel=%s configured=%s",
            relay_url,
            channel,
            bool(private_key),
        )
        if not ready:
            logger.debug(
                "[publish] buzz delivery: skipped - private_key_configured=%s channel=%s",
                bool(private_key),
                channel,
            )
            return False

        from integrations.buzz.delivery import send_buzz_report

        posted, error = send_buzz_report(
            messages.get("slack_text", ""),
            {
                "relay_url": relay_url,
                "private_key": private_key,
                "auth_tag": auth_tag,
                "buzz_path": buzz_path,
                "channel": channel,
            },
        )
        logger.debug("[publish] buzz delivery: posted=%s error=%s", posted, error)
        if not posted:
            logger.warning(
                "[publish] Buzz delivery failed: destination=%s error=%s",
                channel,
                error,
            )
        return True


buzz_delivery_adapter = _BuzzReportDeliveryAdapter()
register_delivery_adapter(buzz_delivery_adapter)

__all__ = ["buzz_delivery_adapter"]
