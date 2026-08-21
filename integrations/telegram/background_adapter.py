"""Telegram delivery for background RCA completion notifications."""

from __future__ import annotations

from infrastructure.delivery.notifications.outbound_registry import (
    BACKGROUND_RCA,
    register_outbound_adapter,
)
from infrastructure.delivery.notifications.rca_summary import summary_sections
from infrastructure.errors import OpenSREError
from infrastructure.scheduling.background_investigations.types import BackgroundInvestigationRecord


def deliver_telegram_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Telegram; return a result string."""
    # Imported lazily: telegram delivery only fires on background-RCA completion, so
    # the telegram client must not load into the base REPL boot import path.
    from infrastructure.delivery.notifications.redaction import redact_token
    from integrations.smtp.delivery import format_background_rca_email
    from integrations.telegram.credentials import load_credentials_from_env
    from integrations.telegram.delivery import send_telegram_report

    try:
        creds = load_credentials_from_env()
    except OpenSREError as exc:
        return f"missing telegram integration: {exc}"

    command, root_cause, top_analysis, next_steps = summary_sections(record)
    _subject, body = format_background_rca_email(
        task_id=record.task_id,
        command=command,
        root_cause=root_cause,
        top_analysis=top_analysis,
        next_steps=next_steps,
        stats=record.stats,
    )
    ok, error = send_telegram_report(
        body,
        {"bot_token": creds.bot_token, "chat_id": creds.chat_id},
        parse_mode="",
    )
    if ok:
        return "sent"
    # The bot token travels in the request URL, and the transport surfaces a
    # non-JSON error body verbatim, so redact before this reaches the record
    # and `/background show`.
    return f"failed: {redact_token(error, creds.bot_token)}"


class _TelegramBackgroundAdapter:
    """Registry adapter wrapping :func:`deliver_telegram_notification`."""

    name = "telegram"
    capabilities = frozenset({BACKGROUND_RCA})

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        return deliver_telegram_notification(record)


telegram_background_adapter = _TelegramBackgroundAdapter()
register_outbound_adapter(telegram_background_adapter)
