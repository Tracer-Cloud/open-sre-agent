"""Background RCA notification helpers."""

from __future__ import annotations

from cli.interactive_shell.runtime.background import BackgroundInvestigationRecord
from platform.notifications.smtp_delivery import format_background_rca_email, send_smtp_report


def deliver_background_notifications(
    *,
    record: BackgroundInvestigationRecord,
    channels: tuple[str, ...],
) -> dict[str, str]:
    """Send configured notifications for a completed background RCA."""
    results: dict[str, str] = {}
    from integrations.catalog import resolve_effective_integrations

    effective_integrations = resolve_effective_integrations()

    for channel in channels:
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
