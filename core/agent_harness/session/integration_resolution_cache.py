"""Helpers for session integration resolution caches."""

from __future__ import annotations

from typing import Any


def has_resolved_integrations(cache: dict[str, Any] | None) -> bool:
    """Return True when the cache holds at least one integration config."""
    if not cache:
        return False
    return any(not str(key).startswith("_") for key in cache)


def has_only_runtime_metadata(cache: dict[str, Any] | None) -> bool:
    """Return True when the cache holds only runtime metadata keys."""
    if not cache:
        return False
    return all(str(key).startswith("_") for key in cache)


def merge_resolved_integrations(
    base: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge integration configs while preserving gateway/runtime metadata keys."""
    merged = dict(base or {})
    merged.update(updates)
    return merged


__all__ = [
    "has_only_runtime_metadata",
    "has_resolved_integrations",
    "merge_resolved_integrations",
]
