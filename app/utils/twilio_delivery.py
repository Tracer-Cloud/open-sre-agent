"""Twilio SMS delivery helper — posts investigation findings via Twilio SMS.

This module is independent of the WhatsApp integration: WhatsApp delivery
lives in :mod:`app.utils.whatsapp_delivery` and the two share no code.
"""

from __future__ import annotations

import logging
from typing import Any

from app.utils.delivery_transport import post_form
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_SMS_LIMIT = 1600
_TWILIO_BASE_URL = "https://api.twilio.com/2010-04-01/Accounts"


def _redact_token(text: str, token: str) -> str:
    """Replace access token with <redacted> to prevent accidental log leakage."""
    if token and token in text:
        return text.replace(token, "<redacted>")
    return text


def post_twilio_sms(
    to: str,
    text: str,
    account_sid: str,
    auth_token: str,
    from_number: str = "",
    messaging_service_sid: str = "",
    status_callback: str = "",
) -> tuple[bool, str, str]:
    """Send an SMS via the Twilio Messaging API.

    Returns ``(success, error, message_sid)``. Either ``from_number`` or
    ``messaging_service_sid`` must be set; if both are provided,
    ``messaging_service_sid`` wins (Twilio's documented precedence).
    """
    if not (from_number or messaging_service_sid):
        return False, "Missing from_number or messaging_service_sid.", ""

    logger.debug("[twilio-sms] post message to %s", to)
    url = f"{_TWILIO_BASE_URL}/{account_sid}/Messages.json"
    payload: dict[str, str] = {
        "To": to.strip(),
        "Body": text,
    }
    if messaging_service_sid:
        payload["MessagingServiceSid"] = messaging_service_sid
    elif from_number:
        payload["From"] = from_number.strip()
    if status_callback:
        payload["StatusCallback"] = status_callback

    response = post_form(url, payload, auth=(account_sid, auth_token), timeout=15.0)
    if not response.ok:
        safe_error = _redact_token(response.error, auth_token) if auth_token else response.error
        logger.warning("[twilio-sms] post exception: %s", safe_error)
        return False, safe_error, ""

    if response.status_code not in (200, 201):
        error_message = str(
            response.data.get("message")
            or response.data.get("error_message")
            or f"HTTP {response.status_code}"
        )
        safe_error = _redact_token(error_message, auth_token) if auth_token else error_message
        logger.warning("[twilio-sms] post failed: %s", safe_error)
        return False, safe_error, ""

    return True, "", str(response.data.get("sid") or "")


def send_twilio_sms_report(
    report: str,
    sms_ctx: dict[str, Any],
) -> tuple[bool, str, str]:
    """Send a truncated report as SMS via Twilio.

    Returns ``(success, error, message_sid)``. ``sms_ctx`` must include
    ``account_sid``, ``auth_token``, ``to``, and either ``from_number`` or
    ``messaging_service_sid``. ``status_callback`` is optional.
    """
    account_sid = str(sms_ctx.get("account_sid") or "")
    auth_token = str(sms_ctx.get("auth_token") or "")
    to = str(sms_ctx.get("to") or "")
    from_number = str(sms_ctx.get("from_number") or "")
    messaging_service_sid = str(sms_ctx.get("messaging_service_sid") or "")
    status_callback = str(sms_ctx.get("status_callback") or "")

    if not account_sid or not auth_token or not to:
        return False, "Missing account_sid, auth_token, or to", ""
    if not (from_number or messaging_service_sid):
        return False, "Missing from_number or messaging_service_sid", ""

    text = truncate(report, _SMS_LIMIT, suffix="…")
    return post_twilio_sms(
        to=to,
        text=text,
        account_sid=account_sid,
        auth_token=auth_token,
        from_number=from_number,
        messaging_service_sid=messaging_service_sid,
        status_callback=status_callback,
    )
