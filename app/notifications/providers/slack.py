"""Slack notification provider implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.notifications.interface import NotificationProvider
from app.utils.slack_delivery import send_slack_report

logger = logging.getLogger(__name__)


from app.notifications.models import NotificationEvent


class SlackProvider(NotificationProvider):
    """Slack notification provider."""

    def send_notification(
        self,
        event: NotificationEvent,
        config: dict[str, Any],
    ) -> tuple[bool, str]:
        """Send an RCA report to Slack."""
        # Slack context can contain channel, thread_ts, access_token, and blocks
        return send_slack_report(
            event.format_text(),
            channel=config.get("channel"),
            thread_ts=config.get("thread_ts"),
            access_token=config.get("access_token"),
            blocks=config.get("blocks") or event.metadata.get("blocks"),
        )

    def probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Verify Slack connectivity."""
        from app.cli.wizard.integration_validators.http_probe_validators import validate_slack_webhook
        
        webhook_url = str(config.get("webhook_url") or "")
        if not webhook_url:
            # If no webhook, maybe we use bot token? 
            # For now, OpenSRE onboarding uses webhook for probe.
            return False, "Missing Slack webhook_url"
            
        result = validate_slack_webhook(webhook_url=webhook_url)
        return result.ok, result.detail
