"""Typed models for Slack message delivery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlackDeliveryTarget:
    """Resolved Slack webhook delivery destination.

    ``webhook_url`` is deliberately excluded from repr so failed assertions,
    tracebacks, or debug logs do not leak the webhook secret.
    """

    webhook_url: str

    def __repr__(self) -> str:
        return "SlackDeliveryTarget(webhook_url=<redacted>)"
