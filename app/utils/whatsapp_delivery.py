"""WhatsApp delivery helper — posts investigation findings via Twilio."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.utils.delivery_transport import post_json
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 4096
_TWILIO_BASE_URL = "https://api.twilio.com/2010-04-01/Accounts"


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
    """Legacy Meta WhatsApp Cloud API sender.

    Returns (success, error, message_id).

    Kept for backward compatibility with modules that still call this API
    shape directly (watchdog/Hermes).
    """
    logger.debug("[whatsapp] post message to %s", to)
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
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


def post_whatsapp_message_twilio(
    to: str,
    text: str,
    account_sid: str,
    auth_token: str,
    from_number: str,
) -> tuple[bool, str, str]:
    """Send a WhatsApp message via Twilio Messaging API.

    Returns (success, error, message_id).
    """
    logger.debug("[whatsapp] post twilio message to %s", to)
    url = f"{_TWILIO_BASE_URL}/{account_sid}/Messages.json"
    twilio_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    payload = {
        "From": from_number,
        "To": twilio_to,
        "Body": text,
    }
    try:
        response = httpx.post(
            url,
            data=payload,
            auth=(account_sid, auth_token),
            timeout=15.0,
            follow_redirects=False,
        )
    except Exception as exc:
        error = _redact_token(str(exc), auth_token)
        logger.warning("[whatsapp] twilio post exception: %s", error)
        return False, error, ""

    error_message = ""
    parsed: dict[str, Any] = {}
    try:
        raw = response.json()
        if isinstance(raw, dict):
            parsed = raw
    except Exception:
        parsed = {}

    if response.status_code not in (200, 201):
        if parsed:
            error_message = str(
                parsed.get("message")
                or parsed.get("error_message")
                or f"HTTP {response.status_code}"
            )
        else:
            error_message = response.text or f"HTTP {response.status_code}"
        error_message = _redact_token(error_message, auth_token)
        logger.warning("[whatsapp] twilio post failed: %s", error_message)
        return False, error_message, ""

    message_id = str(parsed.get("sid") or "")
    return True, "", message_id


def send_whatsapp_report(
    report: str,
    whatsapp_ctx: dict[str, Any],
) -> tuple[bool, str]:
    """Send a truncated report to WhatsApp. Returns (success, error)."""
    account_sid: str = str(whatsapp_ctx.get("account_sid") or "")
    auth_token: str = str(whatsapp_ctx.get("auth_token") or "")
    from_number: str = str(whatsapp_ctx.get("from_number") or "")
    to: str = str(whatsapp_ctx.get("to") or "")
    if not account_sid or not auth_token or not from_number or not to:
        return False, "Missing account_sid, auth_token, from_number, or to"

    text = truncate(report, _MESSAGE_LIMIT, suffix="…")
    post_success, error, _ = post_whatsapp_message_twilio(
        to=to,
        text=text,
        account_sid=account_sid,
        auth_token=auth_token,
        from_number=from_number,
    )
    return (True, "") if post_success else (False, error)
