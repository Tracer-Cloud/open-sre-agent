"""Mattermost delivery for background RCA completion notifications."""

from __future__ import annotations

from surfaces.interactive_shell.runtime.background.rca_summary import summary_sections
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
)


def deliver_mattermost_notification(record: BackgroundInvestigationRecord) -> str:
    """Send the background-RCA completion summary to Mattermost; return a result string.

    Token credentials with a configured ``default_channel`` are preferred;
    the incoming webhook (fixed destination) is the fallback when token
    credentials are absent — the same routing rule as the
    ``mattermost_send_message`` tool.
    """
    # Imported lazily: mattermost delivery only fires on background-RCA completion,
    # so the mattermost client must not load into the base REPL boot import path.
    from integrations.catalog import resolve_effective_integrations
    from integrations.mattermost.delivery import (
        post_mattermost_message,
        post_mattermost_webhook,
    )
    from integrations.smtp.delivery import format_background_rca_email
    from platform.notifications.redaction import redact_token

    entry = resolve_effective_integrations().get("mattermost") or {}
    config = entry.get("config") if isinstance(entry, dict) else None
    if not isinstance(config, dict) or not config:
        return "missing mattermost integration: Mattermost is not configured."

    server_url = str(config.get("server_url") or "")
    auth_token = str(config.get("auth_token") or "")
    webhook_url = str(config.get("webhook_url") or "")
    channel = str(config.get("default_channel") or "")
    has_pat = bool(server_url and auth_token)

    command, root_cause, top_analysis, next_steps = summary_sections(record)
    _subject, body = format_background_rca_email(
        task_id=record.task_id,
        command=command,
        root_cause=root_cause,
        top_analysis=top_analysis,
        next_steps=next_steps,
        stats=record.stats,
    )

    if has_pat and channel:
        ok, error, _message_id = post_mattermost_message(server_url, channel, body, auth_token)
        if ok:
            return "sent"
        # Delivery errors may echo transport detail; the token is redacted by
        # the delivery helper, but redact again at this boundary since the
        # string lands in the record and `/background show`.
        return f"failed: {redact_token(error, auth_token)}"
    if not has_pat and webhook_url:
        ok, error = post_mattermost_webhook(webhook_url, body)
        if ok:
            return "sent"
        return f"failed: {redact_token(error, webhook_url)}"
    if has_pat:
        # Deliberately no webhook fallback here even when one is configured:
        # token credentials say the user works in channel-targeting mode, so a
        # missing default_channel is a configuration gap to surface, not a
        # license to deliver to the webhook's fixed destination (same rule as
        # the mattermost_send_message tool and the cron provider).
        return (
            "missing mattermost integration: no default_channel configured "
            "(set MATTERMOST_DEFAULT_CHANNEL or re-run setup)."
        )
    return (
        "missing mattermost integration: configure token credentials "
        "(server_url, auth_token) or an incoming webhook."
    )
