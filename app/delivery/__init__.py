"""Delivery dispatcher — sends investigation results to all configured channels."""

from __future__ import annotations

from typing import Any

from app.state import InvestigationState


def deliver(state: InvestigationState) -> dict[str, Any]:
    """Format and deliver the investigation report to all configured channels.

    Delegates to the existing generate_report implementation which handles
    Slack, Discord, Telegram, GitLab, and terminal rendering.

    Returns state updates with slack_message and report fields.
    """
    from app.delivery.publish_findings.node import generate_report

    return generate_report(state)
