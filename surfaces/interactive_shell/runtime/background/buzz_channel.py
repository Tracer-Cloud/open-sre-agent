"""Buzz delivery for background RCA completion notifications."""

from __future__ import annotations

from surfaces.interactive_shell.runtime.background.rca_summary import summary_sections
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)


def deliver_buzz_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Buzz; return a result string."""
    # Imported lazily: buzz delivery only fires on background-RCA completion,
    # so the buzz client must not load into the base REPL boot import path.
    from integrations.buzz.delivery import post_buzz_message
    from integrations.catalog import resolve_effective_integrations
    from integrations.smtp.delivery import format_background_rca_email
    from platform.notifications.redaction import redact_token

    entry = resolve_effective_integrations().get("buzz") or {}
    config = entry.get("config") if isinstance(entry, dict) else None
    if not isinstance(config, dict) or not config:
        return "missing buzz integration: Buzz is not configured."

    relay_url = str(config.get("relay_url") or "http://localhost:3000")
    private_key = str(config.get("private_key") or "")
    auth_tag = str(config.get("auth_tag") or "")
    buzz_path = str(config.get("buzz_path") or "buzz")
    channel = str(config.get("default_channel") or "")

    if not private_key:
        return "missing buzz integration: Buzz is not configured."
    if not channel:
        return (
            "missing buzz integration: no default_channel configured "
            "(set BUZZ_DEFAULT_CHANNEL or re-run setup)."
        )

    command, root_cause, top_analysis, next_steps = summary_sections(record)
    _subject, body = format_background_rca_email(
        task_id=record.task_id,
        command=command,
        root_cause=root_cause,
        top_analysis=top_analysis,
        next_steps=next_steps,
        stats=record.stats,
    )

    ok, error, _event_id = post_buzz_message(
        relay_url, channel, body, private_key, auth_tag=auth_tag, buzz_path=buzz_path
    )
    if ok:
        return "sent"
    # Delivery errors may echo transport detail; the key is redacted by the
    # delivery helper, but redact again at this boundary since the string
    # lands in the record and `/background show`.
    return f"failed: {redact_token(error, private_key)}"
