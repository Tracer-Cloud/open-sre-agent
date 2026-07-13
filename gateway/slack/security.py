"""Inbound authorization for Slack gateway messages."""

from __future__ import annotations

from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    message_hash,
)


def authorize_slack_message(
    *,
    user_id: str,
    channel_id: str,
    text: str,
    allowed_user_ids: list[str],
    allow_open_workspace: bool = False,
) -> AuthorizationResult:
    """Authorize one inbound Slack message against the env allowlist and audit-log it.

    Empty ``allowed_user_ids`` is deny-by-default unless ``allow_open_workspace``
    was explicitly enabled at settings load (dogfood escape hatch only).
    """
    if not allowed_user_ids and not allow_open_workspace:
        result = AuthorizationResult(
            allowed=False,
            reason="Slack allowlist is empty; set SLACK_ALLOWED_USERS or SLACK_ALLOW_OPEN_WORKSPACE=1",
        )
        audit_log_inbound_message(
            platform=MessagingPlatform.SLACK.value,
            user_id=user_id,
            chat_id=channel_id,
            message_hash=message_hash(text),
            authorized=False,
            reason=result.reason,
        )
        return result

    policy = MessagingIdentityPolicy(
        inbound_enabled=True,
        allowed_user_ids=list(allowed_user_ids),
        require_dm_pairing=False,
    )
    result = authorize_inbound_message(
        policy=policy,
        user_id=user_id,
        chat_id=channel_id,
        message_text=text,
    )
    audit_log_inbound_message(
        platform=MessagingPlatform.SLACK.value,
        user_id=user_id,
        chat_id=channel_id,
        message_hash=message_hash(text),
        authorized=bool(result),
        reason=result.reason,
    )
    return result
