"""Build all channel-specific report messages from a report context."""

from __future__ import annotations

from dataclasses import dataclass

from core.orchestration.node.publish_findings.context import ReportContext
from core.orchestration.node.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    format_telegram_message,
    format_whatsapp_message,
)


@dataclass(frozen=True)
class ReportMessages:
    """Rendered report bodies for every publish channel."""

    slack_text: str
    telegram_html: str
    whatsapp_text: str
    slack_blocks: list[dict]

    @property
    def sms_text(self) -> str:
        """SMS currently reuses the WhatsApp/plain-text body."""
        return self.whatsapp_text


def build_report_messages(ctx: ReportContext) -> ReportMessages:
    """Render all report channel bodies from a shared context."""
    return ReportMessages(
        slack_text=format_slack_message(ctx),
        telegram_html=format_telegram_message(ctx),
        whatsapp_text=format_whatsapp_message(ctx),
        slack_blocks=build_slack_blocks(ctx),
    )
