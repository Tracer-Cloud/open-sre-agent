"""Background RCA notification helpers."""

from __future__ import annotations

from platform.common.errors import OpenSREError
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
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

            try:
                creds = load_credentials_from_env()
            except OpenSREError as exc:
                results["telegram"] = f"missing telegram integration: {exc}"
                continue

            _subject, body = format_background_rca_email(
                task_id=record.task_id,
                command=record.command,
                root_cause=record.root_cause,
                top_analysis=record.top_analysis,
                next_steps=record.next_steps,
                stats=record.stats,
            )
            ok, error = send_telegram_report(
                body,
                {"bot_token": creds.bot_token, "chat_id": creds.chat_id},
                parse_mode="",
            )
            results["telegram"] = "sent" if ok else f"failed: {error}"
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
