"""Where the Rootly tools get their connection details.

Shared by the read tool and the write tool so neither reimplements the
"resolved source, else environment" fallback — and so the token is protected
in exactly one place.
"""

from __future__ import annotations

import logging
from typing import Any

from integrations.rootly import RootlyConfig, rootly_config_from_env
from integrations.rootly.client import RootlyClient, make_rootly_client

logger = logging.getLogger(__name__)

# The token must beat anything the model sends. ``base_url`` and
# ``timeout_seconds`` are protected with it: pointing an authenticated call at
# a model-chosen host is how a credential leaves the building.
ROOTLY_INJECTED_PARAMS: tuple[str, ...] = ("rootly_token", "rootly_base_url", "rootly_timeout")

SOURCE = "rootly"


def _env_config() -> RootlyConfig | None:
    """``rootly_config_from_env`` without the raise.

    The loader raises on a malformed value so discovery can report it. Tool
    availability is computed on every turn and must not: a bad ``ROOTLY_BASE_URL``
    should make the tool absent, not break the turn.
    """
    try:
        return rootly_config_from_env()
    except Exception as exc:
        logger.warning("[rootly] Ignoring unusable environment config: %s", exc)
        return None


def rootly_available(sources: dict) -> bool:
    """Available once verification has passed, or a token resolves from env."""
    if bool(sources.get(SOURCE, {}).get("connection_verified")):
        return True
    return _env_config() is not None


def rootly_creds(sources: dict) -> dict[str, Any]:
    """Map the resolved source record onto the tools' parameter names.

    The store keeps ``RootlyConfig.model_dump`` field names; the tool schema
    uses ``rootly_``-prefixed ones so they read unambiguously in a tool call.
    """
    record = sources.get(SOURCE, {})
    return {
        "rootly_token": record.get("api_token", ""),
        "rootly_base_url": record.get("base_url", ""),
        "rootly_timeout": record.get("timeout_seconds"),
    }


def resolve_client(
    rootly_token: str | None,
    rootly_base_url: str | None,
    rootly_timeout: float | str | None,
) -> RootlyClient | None:
    """Build a client from injected values, falling back to the environment."""
    token = (rootly_token or "").strip()
    if token:
        return make_rootly_client(
            token,
            base_url=rootly_base_url or "",
            timeout_seconds=rootly_timeout,
        )
    env = _env_config()
    if env is None:
        return None
    return RootlyClient(env)


__all__ = [
    "ROOTLY_INJECTED_PARAMS",
    "SOURCE",
    "resolve_client",
    "rootly_available",
    "rootly_creds",
]
