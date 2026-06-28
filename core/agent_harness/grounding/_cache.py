"""Shared helpers for shell-owned grounding references."""

from __future__ import annotations


def excerpt(body: str, max_chars: int) -> str:
    """Trim a body to ``max_chars``, preferring a paragraph boundary."""
    body = body.strip()
    if len(body) <= max_chars:
        return body
    cutoff = body.rfind("\n\n", 0, max_chars)
    if cutoff < max_chars // 2:
        cutoff = max_chars
    return body[:cutoff].rstrip() + "\n\n[... excerpt truncated ...]\n"


__all__ = ["excerpt"]
