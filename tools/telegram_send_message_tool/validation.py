"""Input normalization and validation for Telegram message actions."""

from __future__ import annotations


def normalize_optional_text(value: str) -> str:
    return str(value or "").strip()


def validate_message(message: str) -> tuple[bool, str, str]:
    normalized = str(message or "").strip()
    if not normalized:
        return False, "", "Message cannot be empty."
    return True, normalized, ""
