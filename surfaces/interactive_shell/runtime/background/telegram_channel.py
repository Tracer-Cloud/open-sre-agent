"""Telegram delivery for background RCA completion notifications."""

from __future__ import annotations

from platform.common.errors import OpenSREError
from surfaces.interactive_shell.runtime.background.rca_summary import summary_sections
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)


def deliver_telegram_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Telegram; return a result string."""
    # Imported lazily: telegram delivery only fires on background-RCA completion, so
    # the telegram client must not load into the base REPL boot import path.
    from integrations.smtp.delivery import format_background_rca_email
    from integrations.telegram.credentials import load_credentials_from_env
    from integrations.telegram.delivery import send_telegram_report
    from platform.notifications.redaction import redact_token

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
