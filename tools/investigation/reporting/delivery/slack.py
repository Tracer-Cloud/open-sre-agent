"""Slack delivery policy for publish findings."""

from __future__ import annotations

import logging

from core.context.state import InvestigationState

logger = logging.getLogger(__name__)


def deliver_slack_report(
    state: InvestigationState,
    message: str,
    blocks: list[dict],
) -> None:
    """Deliver a Slack report and preserve the threaded fail-closed behavior."""
    from integrations.slack.delivery import send_slack_report, swap_reaction

    slack_ctx = state.get("slack_context", {}) or {}
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")
    channel = slack_ctx.get("channel_id")
    token = slack_ctx.get("access_token")
    alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")

    logger.debug("[publish] slack_ctx=%s", slack_ctx)
    report_posted, delivery_error = send_slack_report(
        message,
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
        raise RuntimeError(
            f"[publish] Slack delivery failed: channel={channel}, "
            f"thread_ts={thread_ts}, reason={delivery_error}"
        )
