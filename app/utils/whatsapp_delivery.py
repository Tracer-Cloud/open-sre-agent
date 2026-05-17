"""WhatsApp delivery helper — posts investigation findings via Meta Cloud API."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.delivery_transport import post_json
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 4096
_API_VERSION = "v18.0"


def _redact_token(text: str, token: str) -> str:
    """Replace access token with <redacted> to prevent accidental log leakage."""
    if token and token in text:
        return text.replace(token, "<redacted>")
    return text


def post_whatsapp_message(
    to: str,
    text: str,
    phone_number_id: str,
    access_token: str,
) -> tuple[bool, str, str]:
    """Call Meta WhatsApp Cloud API messages endpoint.

    Returns (success, error, message_id).
    """
    logger.debug("[whatsapp] post message to %s", to)
    url = f"https://graph.facebook.com/{_API_VERSION}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}"}
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    response = post_json(url, payload, headers=headers, timeout=15.0)
    if not response.ok:
        error = _redact_token(response.error, access_token)
        logger.warning("[whatsapp] post message exception: %s", error)
        return False, error, ""
    if response.status_code != 200:
        error_message = ""
        if response.data:
            error_message = str(
                response.data.get("error", {}).get("message")
                or response.data.get("message", "")
                or f"HTTP {response.status_code}"
            )
        else:
            error_message = response.text or f"HTTP {response.status_code}"
        error_message = _redact_token(error_message, access_token)
        logger.warning("[whatsapp] post message failed: %s", error_message)
        return False, error_message, ""

    result = response.data.get("messages", [{}])[0] if isinstance(response.data, dict) else {}
    message_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    return True, "", message_id


def send_whatsapp_report(
    report: str,
    whatsapp_ctx: dict[str, Any],
) -> tuple[bool, str]:
    """Send a truncated report to WhatsApp. Returns (success, error)."""
    access_token: str = str(whatsapp_ctx.get("access_token") or "")
    phone_number_id: str = str(whatsapp_ctx.get("phone_number_id") or "")
    to: str = str(whatsapp_ctx.get("to") or "")
    if not access_token or not phone_number_id or not to:
        return False, "Missing access_token, phone_number_id, or to"

    text = truncate(report, _MESSAGE_LIMIT, suffix="…")
    post_success, error, _ = post_whatsapp_message(
        to=to,
        text=text,
        phone_number_id=phone_number_id,
        access_token=access_token,
    )
    return (True, "") if post_success else (False, error)
