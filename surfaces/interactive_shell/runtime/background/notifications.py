"""Background RCA notification helpers."""

from __future__ import annotations

from platform.common.errors import OpenSREError
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)

# Telegram caps a message at 4096 characters and the transport tail-truncates to fit.
# The RCA body ends with "What to do next" and the stats block, so an unbounded root
# cause would push exactly the actionable sections off the end. Budget each section
# instead: the worst case below stays under the cap, so the tail always survives.
_TELEGRAM_COMMAND_CHARS = 200
_TELEGRAM_ROOT_CAUSE_CHARS = 1000
_TELEGRAM_ITEM_CHARS = 240
_TELEGRAM_MAX_ITEMS = 5


def _telegram_summary_sections(
    record: BackgroundInvestigationRecord,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Return the RCA sections trimmed to fit one Telegram message.

    Email keeps the full report; Telegram is a notification, so it carries a bounded
    summary and points the reader at ``/background show`` for the rest.
    """
    from platform.common.truncation import truncate

    def _items(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            truncate(value, _TELEGRAM_ITEM_CHARS, suffix="…")
            for value in values[:_TELEGRAM_MAX_ITEMS]
        )

    return (
        truncate(record.command, _TELEGRAM_COMMAND_CHARS, suffix="…"),
        truncate(record.root_cause, _TELEGRAM_ROOT_CAUSE_CHARS, suffix="…"),
        _items(record.top_analysis),
        _items(record.next_steps),
    )


def deliver_background_notifications(
    *,
    record: BackgroundInvestigationRecord,
    channels: tuple[str, ...],
) -> dict[str, str]:
    """Send configured notifications for a completed background RCA."""
    # Imported lazily: email delivery only fires on background-RCA completion, so
    # the SMTP client must not load into the base REPL boot import path.
    from integrations.smtp.delivery import format_background_rca_email, send_smtp_report

    results: dict[str, str] = {}
    from integrations.catalog import resolve_effective_integrations

    effective_integrations = resolve_effective_integrations()

    for channel in channels:
        if channel == "telegram":
            # Imported lazily: telegram delivery only fires on background-RCA
            # completion, so the telegram client must not load into the base
            # REPL boot import path.
            from integrations.telegram.credentials import load_credentials_from_env
            from integrations.telegram.delivery import send_telegram_report
            from platform.notifications.redaction import redact_token

            try:
                creds = load_credentials_from_env()
            except OpenSREError as exc:
                results["telegram"] = f"missing telegram integration: {exc}"
                continue

            command, root_cause, top_analysis, next_steps = _telegram_summary_sections(record)
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
            # The bot token travels in the request URL, and the transport surfaces a
            # non-JSON error body verbatim, so redact before this reaches the record
            # and `/background show`.
            results["telegram"] = (
                "sent" if ok else f"failed: {redact_token(error, creds.bot_token)}"
            )
            continue

        if channel != "email":
            results[channel] = "unsupported"
            continue

        smtp_integration = effective_integrations.get("smtp")
        smtp_config = smtp_integration.get("config") if isinstance(smtp_integration, dict) else None
        if not isinstance(smtp_config, dict):
            results["email"] = "missing smtp integration"
            continue

        subject, body = format_background_rca_email(
            task_id=record.task_id,
            command=record.command,
            root_cause=record.root_cause,
            top_analysis=record.top_analysis,
            next_steps=record.next_steps,
            stats=record.stats,
        )
        ok, error = send_smtp_report(report=body, subject=subject, smtp_ctx=smtp_config)
        results["email"] = "sent" if ok else f"failed: {error}"

    return results
