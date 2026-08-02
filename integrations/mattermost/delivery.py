"""Mattermost delivery helper - posts investigation findings via the REST API."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from platform.common.truncation import truncate
from platform.notifications.delivery_errors import extract_http_error
from platform.notifications.delivery_transport import post_json
from platform.notifications.limits import MAX_MESSAGE_SIZE
from platform.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_ATTACHMENT_TEXT_LIMIT = MAX_MESSAGE_SIZE
_REPORT_COLOR = "#E74C3C"


def _mattermost_auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


def post_mattermost_message(
    server_url: str,
    channel_id: str,
    text: str,
    auth_token: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str, str]:
    """Call the Mattermost ``POST /api/v4/posts`` endpoint.

    ``channel_id`` must be the Mattermost channel id, not its display name —
    unlike Rocket.Chat's ``chat.postMessage``, Mattermost's post endpoint does
    not accept a ``#channel`` name.

    Returns True on success, False on expected failures.
    """
    logger.debug("[mattermost] post message params channel_id: %s", channel_id)
    payload: dict[str, Any] = {"channel_id": channel_id, "message": text}
    if attachments:
        payload["props"] = {"attachments": attachments}
    response = post_json(
        url=f"{server_url.rstrip('/')}/api/v4/posts",
        payload=payload,
        headers=_mattermost_auth_headers(auth_token),
    )
    if not response.ok:
        safe_error = redact_token(response.error, auth_token)
        logger.warning("[mattermost] post message exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, auth_token)
        logger.warning("[mattermost] post message failed: %s", safe_error)
        return False, safe_error, ""
    message_id = str(response.data.get("id") or "")
    return True, "", message_id


def post_mattermost_webhook(
    webhook_url: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Post to a Mattermost incoming webhook.

    Returns True on success, False on expected failures. The webhook URL
    embeds its token, so it is redacted from returned errors and logs. Unlike
    Rocket.Chat's webhook (which echoes ``{"success": true}``), Mattermost's
    incoming-webhook endpoint returns a plain-text ``ok`` body on success, so
    the only success signal is the HTTP status.
    """
    payload: dict[str, Any] = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    response = post_json(url=webhook_url, payload=payload)
    if not response.ok:
        safe_error = redact_token(response.error, webhook_url)
        logger.warning("[mattermost] webhook post exception: %s", safe_error)
        return False, safe_error
    if response.status_code != HTTPStatus.OK:
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, webhook_url)
        logger.warning("[mattermost] webhook post failed: %s", safe_error)
        return False, safe_error
    return True, ""


def send_mattermost_report(report: str, mattermost_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Deliver an investigation report via token credentials when configured, else webhook.

    Token-first, webhook-fallback — the same routing rule as
    :class:`integrations.mattermost.alarms.MattermostAlarmDispatcher`,
    :func:`integrations.mattermost.tools.mattermost_send_message_tool.delivery.resolve_target`,
    and :func:`integrations.mattermost.verifier.verify_mattermost` (which probes
    the token endpoint whenever a token is configured, regardless of whether a
    webhook is also set).

    A token without a resolvable channel does **not** fall back to the
    webhook: like :func:`integrations.mattermost.credentials.load_credentials_from_env`,
    a channel-less send with token credentials configured is a configuration
    gap to surface, not license to deliver to a webhook whose destination the
    caller never chose and ``verify_mattermost`` never probed. Falling back
    silently here — as an earlier version of this function did — would let
    ``opensre integrations verify mattermost`` pass (it only checks the token
    is valid, not that a channel is configured) while report delivery quietly
    used an unverified path.
    """
    attachment = {
        "title": "Investigation Complete",
        "text": truncate(report, _ATTACHMENT_TEXT_LIMIT, suffix="…"),
        "color": _REPORT_COLOR,
    }
    server_url: str = str(mattermost_ctx.get("server_url") or "")
    channel: str = str(mattermost_ctx.get("channel") or "")
    auth_token: str = str(mattermost_ctx.get("auth_token") or "")
    if server_url and auth_token:
        if not channel:
            return False, "No channel to deliver to: configure a default_channel."
        posted, error, _ = post_mattermost_message(
            server_url,
            channel,
            "OpenSRE Investigation",
            auth_token,
            attachments=[attachment],
        )
        return (True, "") if posted else (False, error)

    webhook_url: str = str(mattermost_ctx.get("webhook_url") or "")
    posted, error = post_mattermost_webhook(
        webhook_url, "OpenSRE Investigation", attachments=[attachment]
    )
    return (True, "") if posted else (False, error)
