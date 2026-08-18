"""Email delivery for background RCA completion notifications.

The other three channels each had their own module; email was inlined in the
dispatcher, which is why the SMTP config lookup ran on every completion even
when no email channel was configured. It lives here now, so the lookup happens
only when email was actually requested.

Email keeps the full report. The chat channels carry the bounded summary from
``platform.notifications.rca_summary``.
"""

from __future__ import annotations

from platform.background_investigations.types import BackgroundInvestigationRecord
from platform.notifications.outbound_registry import (
    BACKGROUND_RCA,
    register_outbound_adapter,
)


def deliver_email_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion report by email; return a result string."""
    # Imported lazily: email delivery only fires on background-RCA completion, so
    # the SMTP client must not load into the base REPL boot import path.
    from integrations.catalog import resolve_effective_integrations
    from integrations.smtp.delivery import format_background_rca_email, send_smtp_report

    smtp_integration = resolve_effective_integrations().get("smtp")
    smtp_config = smtp_integration.get("config") if isinstance(smtp_integration, dict) else None
    if not isinstance(smtp_config, dict):
        return "missing smtp integration"

    subject, body = format_background_rca_email(
        task_id=record.task_id,
        command=record.command,
        root_cause=record.root_cause,
        top_analysis=record.top_analysis,
        next_steps=record.next_steps,
        stats=record.stats,
    )
    ok, error = send_smtp_report(report=body, subject=subject, smtp_ctx=smtp_config)
    return "sent" if ok else f"failed: {error}"


class _EmailBackgroundAdapter:
    """Registry adapter wrapping :func:`deliver_email_notification`."""

    name = "email"
    capabilities = frozenset({BACKGROUND_RCA})

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        return deliver_email_notification(record)


email_background_adapter = _EmailBackgroundAdapter()
register_outbound_adapter(email_background_adapter)
