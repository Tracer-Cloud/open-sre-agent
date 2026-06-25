"""Reusable field-validator factories for integration config models.

Each factory returns a plain callable suitable for use as the body of a
``@field_validator(..., mode="before")`` decorator.  Keeping the logic here
removes the ~50 near-identical one-liners scattered across config_models.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _strip(value: object, default: str) -> str:
    normalized = str(value or default).strip()
    return normalized or default


def normalize_str(default: str = "") -> Callable[[Any], str]:
    """strip-or-empty; optional fallback."""

    def _v(value: object) -> str:
        return str(value or "").strip() or default

    return _v


def normalize_url(default: str = "") -> Callable[[Any], str]:
    """strip + rstrip('/') + fallback."""

    def _v(value: object) -> str:
        normalized = str(value or default).strip().rstrip("/")
        return normalized or default

    return _v


def normalize_with_default(default: str) -> Callable[[Any], str]:
    """strip, fall back to *default* when blank."""

    def _v(value: object) -> str:
        return _strip(value, default)

    return _v


def normalize_bearer(default: str = "") -> Callable[[Any], str]:
    """Strip and drop a leading ``Bearer `` prefix."""

    def _v(value: object) -> str:
        text = str(value or "").strip()
        if text.lower().startswith("bearer "):
            text = text.split(None, 1)[1].strip()
        return text or default

    return _v


def normalize_bool_str() -> Callable[[Any], bool]:
    """Accept bool, None, or common string truthy/falsy representations."""
    _TRUE = {"1", "true", "yes", "on"}
    _FALSE = {"0", "false", "no", "off"}

    def _v(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return True
        text = str(value).strip().lower()
        if text in _FALSE:
            return False
        if text in _TRUE:
            return True
        return bool(value)

    return _v
