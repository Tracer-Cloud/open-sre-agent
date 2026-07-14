"""Argument validation for the agent memory tools."""

from __future__ import annotations

from typing import Any

from core.domain.memory import MEMORY_TYPES, is_valid_slug, slugify


def normalize_name(name: Any) -> str | None:
    """Slugify a tool-supplied memory name; ``None`` when nothing usable remains."""
    if not isinstance(name, str):
        return None
    slug = slugify(name)
    return slug if is_valid_slug(slug) else None


def validate_remember_args(
    name: Any, memory_type: Any, description: Any, content: Any
) -> dict[str, Any] | None:
    """Return a structured error dict for bad arguments, or ``None`` when valid."""
    if normalize_name(name) is None:
        return {
            "error": "invalid_name",
            "detail": "name must contain letters or digits (it is normalized to kebab-case)",
        }
    if memory_type not in MEMORY_TYPES:
        return {
            "error": "invalid_type",
            "detail": f"type must be one of {', '.join(MEMORY_TYPES)}",
        }
    if not isinstance(description, str) or not description.strip():
        return {"error": "empty_description", "detail": "description must be a non-empty string"}
    if not isinstance(content, str) or not content.strip():
        return {"error": "empty_content", "detail": "content must be a non-empty string"}
    return None


__all__ = ["normalize_name", "validate_remember_args"]
