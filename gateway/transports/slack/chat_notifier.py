"""Slack implementation of ChatNotifier for posting investigation results."""

from __future__ import annotations

import logging
from collections import OrderedDict

from gateway.core.chat import ChatDeliveryTarget, DetachedInvestigationAck
from gateway.transports.slack.client import (
    Blocks,
    SlackMessagingClient,
    mark_detached_done,
    mark_turn_failed,
)
from gateway.transports.slack.feedback import feedback_block
from gateway.transports.slack.output_sink import (
    SLACK_MAX_MARKDOWN_BLOCK_CHARS,
    SLACK_MAX_MESSAGE_CHARS,
    SLACK_MAX_TASK_UPDATE_CHARS,
    ai_disclosure_footer_block,
)
from platform.common.truncation import truncate

logger = logging.getLogger(__name__)


class SlackChatNotifier:
    """Posts investigation updates to Slack threads."""

    def __init__(self, *, slack_client: SlackMessagingClient) -> None:
        self._client = slack_client
        # Bounded dict to track ack message timestamps for stage updates
        self._ack_timestamps: OrderedDict[str, str] = OrderedDict()
        self._max_tracked_messages = 256

    def post_ack(self, target: ChatDeliveryTarget, ack: DetachedInvestigationAck) -> str | None:
        """Post the acknowledgment; return the platform message id to edit for stage updates."""
        try:
            ts = self._post_message(target, ack.message)
            if ts:
                # Track the timestamp for future stage updates
                self._ack_timestamps[ack.investigation_id] = ts
                # Cap the size of the dict
                while len(self._ack_timestamps) > self._max_tracked_messages:
                    self._ack_timestamps.popitem(last=False)
            return ts
        except Exception:
            logger.exception(
                "[slack-notifier] failed to post ack for investigation %s",
                ack.investigation_id,
            )
            return None

    def update_stage(self, target: ChatDeliveryTarget, stage: str, investigation_id: str) -> None:
        """Edit the acknowledgment in place to show the current pipeline stage."""
        message = f"🔄 Investigation {investigation_id[:8]}: {stage}"
        # Clamp to Slack task update limit
        message = truncate(message, SLACK_MAX_TASK_UPDATE_CHARS, suffix="…")

        try:
            # Try to update the existing ack message
            ts = self._ack_timestamps.get(investigation_id)
            if ts:
                success = self._client.update_message(
                    channel=target.channel_id,
                    ts=ts,
                    text=message,
                )
                if success:
                    return
                # Fall through to post fresh if update failed

            # Post fresh if we don't have the ts or update failed
            new_ts = self._post_message(target, message)
            if new_ts:
                self._ack_timestamps[investigation_id] = new_ts
        except Exception:
            logger.exception(
                "[slack-notifier] failed to post stage update for investigation %s",
                investigation_id,
            )

    def deliver_final(self, target: ChatDeliveryTarget, report: str, investigation_id: str) -> bool:
        """Post the final report; return whether it reached the thread."""
        prefix = f"✅ Investigation {investigation_id[:8]} complete:\n\n"
        # Clamp the report to stay within Slack limits
        available_chars = SLACK_MAX_MESSAGE_CHARS - len(prefix)
        truncated_report = truncate(report, available_chars, suffix="…")
        message = prefix + truncated_report

        # The report is the model's own investigation output — it gets the same
        # provenance footer and feedback buttons an ordinary reply carries.
        # Falls back to plain text past the markdown block's 12k cap, same as
        # a regular turn reply (SlackOutputSink._final_blocks).
        blocks: Blocks | None = None
        if len(message) <= SLACK_MAX_MARKDOWN_BLOCK_CHARS:
            blocks = [
                {"type": "markdown", "text": message},
                ai_disclosure_footer_block(),
                feedback_block(),
            ]

        try:
            ts = self._post_message(target, message, blocks=blocks)
            return ts is not None
        except Exception:
            logger.exception(
                "[slack-notifier] failed to deliver final report for investigation %s",
                investigation_id,
            )
            return False

    def report_failure(
        self, target: ChatDeliveryTarget, error_summary: str, investigation_id: str
    ) -> None:
        """Report investigation failure with generic error message."""
        message = f"❌ Investigation {investigation_id[:8]} failed: {error_summary}"
        try:
            self._post_message(target, message)
        except Exception:
            logger.exception(
                "[slack-notifier] failed to report failure for investigation %s",
                investigation_id,
            )

    def mark_origin_complete(self, target: ChatDeliveryTarget) -> None:
        """Signal success on the message that asked; best-effort, never raises."""
        if not target.origin_message_id:
            return
        try:
            mark_detached_done(
                self._client, channel=target.channel_id, timestamp=target.origin_message_id
            )
        except Exception:
            logger.exception(
                "[slack-notifier] failed to mark origin message complete for %s",
                target.origin_message_id,
            )

    def mark_origin_failed(self, target: ChatDeliveryTarget) -> None:
        """Signal failure on the message that asked; best-effort, never raises."""
        if not target.origin_message_id:
            return
        try:
            mark_turn_failed(
                self._client, channel=target.channel_id, timestamp=target.origin_message_id
            )
        except Exception:
            logger.exception(
                "[slack-notifier] failed to mark origin message failed for %s",
                target.origin_message_id,
            )

    def _post_message(
        self, target: ChatDeliveryTarget, text: str, *, blocks: Blocks | None = None
    ) -> str | None:
        """Post a message to Slack using the delivery target."""
        return self._client.post_message(
            channel=target.channel_id,
            text=text,
            thread_ts=target.thread_ts,
            blocks=blocks,
        )


def create_slack_notifier(slack_client: SlackMessagingClient) -> SlackChatNotifier:
    """Factory function to create Slack notifier with client."""
    return SlackChatNotifier(slack_client=slack_client)


def register_slack_notifier(slack_client: SlackMessagingClient) -> None:
    """Register Slack notifier with the global registry."""
    from config.constants import PLATFORM_SLACK
    from gateway.core.chat import get_chat_notifier_registry

    notifier = create_slack_notifier(slack_client)
    registry = get_chat_notifier_registry()
    registry.register(PLATFORM_SLACK, notifier)
    logger.info("[slack-notifier] registered with chat notifier registry")
