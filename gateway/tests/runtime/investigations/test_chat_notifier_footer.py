"""Pins the AI-disclosure footer and feedback buttons on a delivered report.

``SlackChatNotifier.deliver_final`` posts through a separate path from an
ordinary turn reply (``SlackOutputSink``), and was never wired to the same
provenance footer / feedback buttons every other model-generated reply
carries — confirmed missing on a live delivered report.
"""

from __future__ import annotations

from gateway.core.chat import ChatDeliveryTarget
from gateway.transports.slack.chat_notifier import SlackChatNotifier
from gateway.transports.slack.output_sink import (
    AI_DISCLOSURE,
    SLACK_MAX_MARKDOWN_BLOCK_CHARS,
)

from .conftest import FakeSlackClient


def test_deliver_final_carries_the_ai_disclosure_and_feedback_buttons(
    slack_client: FakeSlackClient,
    slack_notifier: SlackChatNotifier,
    delivery_target: ChatDeliveryTarget,
) -> None:
    delivered = slack_notifier.deliver_final(
        delivery_target, "## Root cause\n\nDisk full.", "inv-1"
    )

    assert delivered is True
    assert len(slack_client.posted) == 1
    blocks = slack_client.posted[0]["blocks"]
    assert blocks is not None
    footer_texts = [
        el["text"] for block in blocks if block["type"] == "context" for el in block["elements"]
    ]
    assert any(AI_DISCLOSURE in text for text in footer_texts)
    assert any(block["type"] == "context_actions" for block in blocks)


def test_deliver_final_falls_back_to_plain_text_past_the_markdown_block_cap(
    slack_client: FakeSlackClient,
    slack_notifier: SlackChatNotifier,
    delivery_target: ChatDeliveryTarget,
) -> None:
    """An over-long report must still be delivered — Slack rejects an over-cap markdown block outright."""
    huge_report = "x" * (SLACK_MAX_MARKDOWN_BLOCK_CHARS + 1_000)

    delivered = slack_notifier.deliver_final(delivery_target, huge_report, "inv-1")

    assert delivered is True
    assert slack_client.posted[0]["blocks"] is None
