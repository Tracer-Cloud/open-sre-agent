"""Env-var names for runtime-fact probes (workspace identity, capabilities)."""

from __future__ import annotations

from typing import Final

# Explicit workspace repo override (``owner/repo`` or GitHub URL).
OPENSRE_WORKSPACE_REPO_ENV: Final = "OPENSRE_WORKSPACE_REPO"

# Common CI / deploy injectors for the same identity fact.
GITHUB_REPOSITORY_ENV: Final = "GITHUB_REPOSITORY"
GITHUB_REPO_ENV: Final = "GITHUB_REPO"

# Opt-in sandboxed network egress (default blocked — matches sandbox tool).
OPENSRE_ALLOW_NETWORK_ENV: Final = "OPENSRE_ALLOW_NETWORK"

WORKSPACE_REPO_ENV_KEYS: Final[tuple[str, ...]] = (
    OPENSRE_WORKSPACE_REPO_ENV,
    GITHUB_REPOSITORY_ENV,
    GITHUB_REPO_ENV,
)

__all__ = [
    "GITHUB_REPO_ENV",
    "GITHUB_REPOSITORY_ENV",
    "OPENSRE_ALLOW_NETWORK_ENV",
    "OPENSRE_WORKSPACE_REPO_ENV",
    "WORKSPACE_REPO_ENV_KEYS",
]
