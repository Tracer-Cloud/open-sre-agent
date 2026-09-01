"""Configure the local GitHub integration from a personal-account handoff."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from integrations.github.mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_TOOLSETS,
    DEFAULT_GITHUB_MCP_URL,
)
from integrations.store import get_integration, remove_integration, upsert_integration

_ACCOUNT_AUTH_SOURCE = "opensre_account"


@dataclass(frozen=True)
class PersonalGitHubSnapshot:
    """Previous GitHub store record used to roll back a failed login."""

    integration: dict[str, Any] | None


def _restore_snapshot(snapshot: PersonalGitHubSnapshot) -> None:
    if snapshot.integration:
        upsert_integration("github", snapshot.integration)
    else:
        remove_integration("github")


def configure_personal_github(*, access_token: str, username: str) -> PersonalGitHubSnapshot:
    """Persist the token only in the owner-only ``~/.opensre`` store."""
    if not access_token.strip() or not username.strip():
        raise ValueError("A GitHub access token and username are required.")
    snapshot = PersonalGitHubSnapshot(get_integration("github"))
    try:
        upsert_integration(
            "github",
            {
                "instances": [
                    {
                        "name": "default",
                        "tags": {"auth_source": _ACCOUNT_AUTH_SOURCE},
                        "credentials": {
                            "mode": str(DEFAULT_GITHUB_MCP_MODE),
                            "url": DEFAULT_GITHUB_MCP_URL,
                            "auth_token": access_token.strip(),
                            "toolsets": list(DEFAULT_GITHUB_MCP_TOOLSETS),
                            "username": username.strip(),
                        },
                    }
                ]
            },
        )
    except Exception:
        with suppress(Exception):
            _restore_snapshot(snapshot)
        raise
    return snapshot


def restore_personal_github(snapshot: PersonalGitHubSnapshot) -> None:
    """Restore the GitHub integration that existed before account login."""
    _restore_snapshot(snapshot)


def disconnect_personal_github() -> bool:
    """Remove the GitHub integration created by personal account login."""
    integration = get_integration("github")
    instances = integration.get("instances") if integration else None
    first = instances[0] if isinstance(instances, list) and instances else None
    tags = first.get("tags") if isinstance(first, dict) else None
    if not isinstance(tags, dict) or tags.get("auth_source") != _ACCOUNT_AUTH_SOURCE:
        return False
    return remove_integration("github")


__all__ = [
    "PersonalGitHubSnapshot",
    "configure_personal_github",
    "disconnect_personal_github",
    "restore_personal_github",
]
