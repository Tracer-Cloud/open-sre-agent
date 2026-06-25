"""Telegram integration verifier — Bot API getMe probe."""

from __future__ import annotations

from typing import Any

import requests

from app.integrations.verification import register_verifier, result


@register_verifier("telegram")
def verify_telegram(source: str, config: dict[str, Any]) -> dict[str, str]:
    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("telegram", source, "missing", "Missing bot_token.")

    try:
        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return result("telegram", source, "failed", f"Telegram API check failed: {exc}")

    if not payload.get("ok"):
        return result(
            "telegram",
            source,
            "failed",
            f"Telegram API check failed: {payload.get('description', 'unknown error')}",
        )

    user = payload.get("result", {})
    username = str(user.get("username", "")).strip()
    return result(
        "telegram",
        source,
        "passed",
        f"Connected to Telegram bot @{username or 'unknown'}.",
    )
