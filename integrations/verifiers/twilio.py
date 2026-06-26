"""Twilio integration verifier: account auth + SMS channel readiness.

A "passed" result confirms the account credentials authenticate and the
SMS channel has a usable sender (``from_number`` or
``messaging_service_sid``). WhatsApp is verified separately via the
standalone ``whatsapp`` integration.
"""

from __future__ import annotations

from typing import Any

import requests

from integrations.verification import register_verifier, result


@register_verifier("twilio")
def verify_twilio(source: str, config: dict[str, Any]) -> dict[str, str]:
    account_sid = str(config.get("account_sid", "")).strip()
    auth_token = str(config.get("auth_token", "")).strip()
    if not account_sid:
        return result("twilio", source, "missing", "Missing account_sid.")
    if not auth_token:
        return result("twilio", source, "missing", "Missing auth_token.")

    try:
        response = requests.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
            auth=(account_sid, auth_token),
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return result("twilio", source, "failed", f"Twilio API check failed: {exc}")

    friendly_name = str(payload.get("friendly_name", "")).strip() or account_sid

    sms_cfg = config.get("sms") or {}
    sms_ready = bool(sms_cfg.get("enabled")) and bool(
        str(sms_cfg.get("from_number") or "").strip()
        or str(sms_cfg.get("messaging_service_sid") or "").strip()
    )

    if not sms_ready:
        return result(
            "twilio",
            source,
            "failed",
            (
                f"Connected to Twilio account {friendly_name} but the SMS channel "
                "is not ready. Enable SMS and set a from_number or messaging_service_sid."
            ),
        )

    return result(
        "twilio",
        source,
        "passed",
        f"Connected to Twilio account {friendly_name}; SMS channel ready.",
    )
