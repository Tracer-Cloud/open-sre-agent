"""Shared helpers for timeout environment parsing."""

from __future__ import annotations

import os


def resolve_timeout_from_env(
    *,
    env_key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Resolve timeout from an env var with defaulting + clamping.

    Invalid, empty, and non-positive values fall back to ``default``.
    """
    raw = os.environ.get(env_key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return max(minimum, min(value, maximum))
