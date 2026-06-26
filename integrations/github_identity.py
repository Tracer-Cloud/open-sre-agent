"""Lightweight GitHub identity helpers for UI and analytics.

Kept separate from :mod:`integrations.github_login` so callers like the welcome
banner can read the saved handle without importing the heavy GitHub MCP stack.
"""

from __future__ import annotations


def saved_github_username() -> str:
    """Return the persisted GitHub login from the integration store, or "".

    Best-effort and never raises: callers like the welcome banner and analytics
    re-identify must work even when the store is unreadable.
    """
    try:
        from integrations.store import get_integration

        record = get_integration("github")
        if not record:
            return ""
        credentials = record.get("credentials") or {}
        return str(credentials.get("username") or "").strip()
    except Exception:
        return ""
