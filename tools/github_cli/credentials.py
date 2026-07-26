"""GitHub credential helpers for github_cli without importing ``integrations``.

``tools`` and ``integrations`` are layer peers under ``.importlinter.strict``;
vendor helpers used here are inlined (same pattern as ``architecture_issue_tool``).
"""

from __future__ import annotations

import os
from typing import Any


def resolve_github_token(
    explicit: str | None = None, *, allow_env_fallback: bool = True
) -> str:
    """Resolve a GitHub token from explicit input or standard env vars."""
    if explicit is not None:
        token = explicit.strip()
        if token or not allow_env_fallback:
            return token
    if not allow_env_fallback:
        return ""
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()


def github_source_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("github", {}).get("connection_verified"))


def github_creds(gh: dict[str, Any]) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
    creds: dict[str, Any] = {}
    token = gh.get("github_token") or gh.get("auth_token")
    if token:
        creds["github_token"] = token
    if gh.get("connection_verified"):
        creds["allow_env_fallback"] = False
    return creds


__all__ = [
    "github_creds",
    "github_source_available",
    "resolve_github_token",
]
