"""Rocket.Chat delivery helper - posts investigation findings via the REST API."""

from __future__ import annotations

import logging
from typing import Any

from platform.common.truncation import truncate
from platform.notifications.delivery_errors import extract_http_error
from platform.notifications.delivery_transport import post_json
from platform.notifications.limits import MAX_MESSAGE_SIZE
from platform.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_ATTACHMENT_TEXT_LIMIT = MAX_MESSAGE_SIZE
_REPORT_COLOR = "#E74C3C"


def _rocketchat_auth_headers(auth_token: str, user_id: str) -> dict[str, str]:
    # ``Content-Type: application/json`` is set automatically by httpx when
    # the request uses the ``json=`` kwarg, so we only need to add auth.
    return {"X-Auth-Token": auth_token, "X-User-Id": user_id}


def post_rocketchat_message(
    server_url: str,
    channel: str,
    text: str,
    auth_token: str,
    user_id: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str, str]:
    """Call the Rocket.Chat ``chat.postMessage`` endpoint.

    Returns True on success, False on expected failures.
    """
    logger.debug("[rocketchat] post message params channel: %s", channel)
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if attachments:
        payload["attachments"] = attachments
    response = post_json(
        url=f"{server_url.rstrip('/')}/api/v1/chat.postMessage",
        payload=payload,
        headers=_rocketchat_auth_headers(auth_token, user_id),
    )
    if not response.ok:
        safe_error = redact_token(response.error, auth_token)
        logger.warning("[rocketchat] post message exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code != 200 or response.data.get("success") is not True:
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, auth_token)
        logger.warning("[rocketchat] post message failed: %s", safe_error)
        return False, safe_error, ""
    message = response.data.get("message")
    message_id = str(message.get("_id") or "") if isinstance(message, dict) else ""
    return True, "", message_id


def post_rocketchat_webhook(
    webhook_url: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Post to a Rocket.Chat incoming webhook.

    Returns True on success, False on expected failures. The webhook URL
    embeds its token, so it is redacted from returned errors and logs.
    """
    payload: dict[str, Any] = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    response = post_json(url=webhook_url, payload=payload)
    if not response.ok:
        safe_error = redact_token(response.error, webhook_url)
        logger.warning("[rocketchat] webhook post exception: %s", safe_error)
        return False, safe_error
    if response.status_code != 200 or response.data.get("success") is not True:
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, webhook_url)
        logger.warning("[rocketchat] webhook post failed: %s", safe_error)
        return False, safe_error
    return True, ""


def send_rocketchat_report(report: str, rocketchat_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Deliver an investigation report via webhook when configured, else PAT."""
    attachment = {
        "title": "Investigation Complete",
        "text": truncate(report, _ATTACHMENT_TEXT_LIMIT, suffix="…"),
        "color": _REPORT_COLOR,
    }
    webhook_url: str = str(rocketchat_ctx.get("webhook_url") or "")
    if webhook_url:
        posted, error = post_rocketchat_webhook(
            webhook_url, "OpenSRE Investigation", attachments=[attachment]
        )
        return (True, "") if posted else (False, error)

    server_url: str = str(rocketchat_ctx.get("server_url") or "")
    channel: str = str(rocketchat_ctx.get("channel") or "")
    auth_token: str = str(rocketchat_ctx.get("auth_token") or "")
    user_id: str = str(rocketchat_ctx.get("user_id") or "")
    posted, error, _ = post_rocketchat_message(
        server_url,
        channel,
        "OpenSRE Investigation",
        auth_token,
        user_id,
        attachments=[attachment],
    )
    return (True, "") if posted else (False, error)
