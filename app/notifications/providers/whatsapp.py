"""WhatsApp notification provider implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.notifications.interface import NotificationProvider
from app.utils.delivery_transport import post_json
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

# WhatsApp message limit is generally high, but we'll use a safe default.
_MESSAGE_LIMIT = 4096


from app.notifications.models import NotificationEvent


class WhatsAppProvider(NotificationProvider):
    """
    WhatsApp notification provider.
    Initial implementation uses a mock/webhook bridge for testing.
    """

    def send_notification(
        self,
        event: NotificationEvent,
        config: dict[str, Any],
    ) -> tuple[bool, str]:
        """Send an RCA report to WhatsApp."""
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            return False, "Missing WhatsApp webhook_url"

        text = truncate(event.format_text(), _MESSAGE_LIMIT, suffix="…")
        payload = {"text": text}

        # Handle potential phone number or group ID in config
        phone = config.get("phone_number") or config.get("phone")
        if phone:
            payload["phone"] = phone

        response = post_json(url=webhook_url, payload=payload, timeout=15.0)

        if not response.ok:
            return False, f"WhatsApp delivery failed: {response.error}"

        if not 200 <= response.status_code < 300:
            return False, f"WhatsApp API returned HTTP {response.status_code}: {response.text[:200]}"

        logger.info("[whatsapp] report delivered successfully to %s", webhook_url)
        return True, ""

    def probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Verify WhatsApp connectivity."""
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            return False, "Missing WhatsApp webhook_url"

        # Simple health check ping to the bridge
        payload = {"type": "probe", "text": "OpenSRE connectivity check"}
        response = post_json(url=webhook_url, payload=payload, timeout=10.0)

        if response.ok and 200 <= response.status_code < 300:
            return True, "WhatsApp bridge connected successfully."

        return False, f"Connectivity check failed: {response.error or f'HTTP {response.status_code}'}"
