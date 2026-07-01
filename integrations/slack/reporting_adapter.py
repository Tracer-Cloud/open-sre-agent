"""Slack ``ReportDeliveryAdapter`` and Slack-reactions port implementation.

Registers itself into the platform-level registries at import time so
``tools.investigation.reporting.delivery.dispatch`` and
``tools.investigation.stages.intake.node`` never import from
``integrations.slack`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)
from platform.reporting.slack_reactions import (
    SlackReactionsPort,
    register_slack_reactions_port,
)

logger = logging.getLogger(__name__)


class _SlackReportDeliveryAdapter:
    """Slack delivery adapter — always attempts delivery when Slack context is present.

    Slack is treated as the "primary" channel: it is dispatched first (a
    successful post updates the investigation's own thread) and its blocks are
    reused across other channels. The dispatch loop passes the same shared
    ``blocks`` list so this adapter can decorate the returned Slack blocks
    without vendors overwriting each other.
    """

    name = "slack"

    def build_action_blocks(
        self,
        investigation_url: str,
        investigation_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return the shared Slack Block Kit action blocks for the investigation.

        Kept on the adapter so the dispatch loop asks the Slack integration for
        its own blocks through the registry instead of importing
        ``integrations.slack.delivery`` directly.
        """
        from integrations.slack.delivery import build_action_blocks

        return build_action_blocks(investigation_url, investigation_id)

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],
    ) -> bool:
        from integrations.slack.delivery import send_slack_report, swap_reaction

        slack_ctx = state.get("slack_context", {}) or {}
        thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")
        channel = slack_ctx.get("channel_id")
        token = slack_ctx.get("access_token")
        alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")

        logger.debug("[publish] slack_ctx=%s", slack_ctx)
        report_posted, delivery_error = send_slack_report(
            messages.get("slack_text", ""),
            channel=channel,
            thread_ts=thread_ts,
            access_token=token,
            blocks=blocks,
        )
        logger.debug(
            "[publish] slack delivery: posted=%s channel=%s thread_ts=%s error=%s",
            report_posted,
            channel,
            thread_ts,
            delivery_error,
        )

        if report_posted and token and channel and alert_ts:
            swap_reaction("eyes", "clipboard", channel, alert_ts, token)
        elif thread_ts and not report_posted:
            # Preserve the historical fail-closed behavior when Slack is the
            # thread that triggered the investigation — we cannot silently drop
            # the report there.
            raise RuntimeError(
                f"[publish] Slack delivery failed: channel={channel}, "
                f"thread_ts={thread_ts}, reason={delivery_error}"
            )
        return True


class _SlackReactionsPort(SlackReactionsPort):
    """Concrete Slack reactions port backed by ``integrations.slack.delivery``."""

    def add_reaction(self, emoji: str, channel: str, timestamp: str, token: str) -> None:
        from integrations.slack.delivery import add_reaction

        add_reaction(emoji, channel, timestamp, token)

    def swap_reaction(
        self,
        remove_emoji: str,
        add_emoji: str,
        channel: str,
        timestamp: str,
        token: str,
    ) -> None:
        from integrations.slack.delivery import swap_reaction

        swap_reaction(remove_emoji, add_emoji, channel, timestamp, token)


slack_delivery_adapter = _SlackReportDeliveryAdapter()
slack_reactions_port = _SlackReactionsPort()

register_delivery_adapter(slack_delivery_adapter)
register_slack_reactions_port(slack_reactions_port)


__all__ = ["slack_delivery_adapter", "slack_reactions_port"]
