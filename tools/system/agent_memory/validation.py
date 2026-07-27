"""Argument validation for the agent memory tools."""

from __future__ import annotations

from typing import Any

from core.domain.memory import MEMORY_TYPES, find_memory_safety_issues, is_valid_slug, slugify

DEFAULT_RECALL_LIMIT = 5
MAX_RECALL_LIMIT = 20


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
    issues = find_memory_safety_issues(description, content)
    if issues:
        return {
            "error": "sensitive_content",
            "detail": "memory was not saved because it appears to contain a secret or credential",
            "rules": [issue.rule for issue in issues],
        }
    return None


def normalize_recall_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_RECALL_LIMIT
    limit = int(value)
    return min(max(limit, 0), MAX_RECALL_LIMIT)


__all__ = [
    "DEFAULT_RECALL_LIMIT",
    "MAX_RECALL_LIMIT",
    "normalize_name",
    "normalize_recall_limit",
    "validate_remember_args",
]
