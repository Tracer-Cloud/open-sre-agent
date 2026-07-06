"""Credential/environment resolution for the Python execution tool."""

from __future__ import annotations

from typing import Any

from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"


def github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract GitHub credentials from resolved integration sources."""
    gh = sources.get("github", {})
    token = github_creds(gh).get("github_token") if gh else None
    return {"github_token": token} if token else {}


def execution_env(*, github_token: str | None = None) -> tuple[dict[str, str], list[str]]:
    """Return approved env vars for generated Python code plus credential labels."""
    env: dict[str, str] = {}
    available: list[str] = []

    token = resolve_github_token(github_token)
    if token:
        env[GITHUB_TOKEN_ENV] = token
        available.append("github")

    return env, available


__all__ = ["GITHUB_TOKEN_ENV", "execution_env", "github_extract_params"]
