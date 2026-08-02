"""Mattermost ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.mattermost`` directly (same layering rule as the other vendor
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


class _MattermostReportDeliveryAdapter:
    """Mattermost delivery adapter — posts to a channel when credentials are set."""

    name = "mattermost"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        mattermost_creds = resolved.get("mattermost") if isinstance(resolved, dict) else None
        if not mattermost_creds:
            logger.debug("[publish] mattermost delivery: no mattermost integration configured")
            return False

        from core.state.channel_context import get_channel_context

        mattermost_ctx = get_channel_context(state, "mattermost")
        server_url = mattermost_ctx.get("server_url") or mattermost_creds.get("server_url", "")
        auth_token = mattermost_ctx.get("auth_token") or mattermost_creds.get("auth_token", "")
        webhook_url = mattermost_ctx.get("webhook_url") or mattermost_creds.get("webhook_url", "")
        channel = mattermost_ctx.get("channel") or mattermost_creds.get("default_channel", "")
        pat_ready = bool(server_url and auth_token and channel)
        logger.debug(
            "[publish] mattermost delivery: server_url=%s channel=%s "
            "auth_configured=%s webhook_configured=%s",
            server_url,
            channel,
            bool(auth_token),
            bool(webhook_url),
        )
        if not webhook_url and not pat_ready:
            logger.debug(
                "[publish] mattermost delivery: skipped - auth_configured=%s "
                "channel=%s webhook_configured=%s",
                bool(auth_token),
                channel,
                bool(webhook_url),
            )
            return False

        from integrations.mattermost.delivery import send_mattermost_report

        posted, error = send_mattermost_report(
            messages.get("slack_text", ""),
            {
                "server_url": server_url,
                "auth_token": auth_token,
                "webhook_url": webhook_url,
                "channel": channel,
            },
        )
        logger.debug("[publish] mattermost delivery: posted=%s error=%s", posted, error)
        if not posted:
            destination = channel or ("webhook" if webhook_url else "unknown")
            logger.warning(
                "[publish] Mattermost delivery failed: destination=%s error=%s",
                destination,
                error,
            )
        return True


mattermost_delivery_adapter = _MattermostReportDeliveryAdapter()
register_delivery_adapter(mattermost_delivery_adapter)

__all__ = ["mattermost_delivery_adapter"]
