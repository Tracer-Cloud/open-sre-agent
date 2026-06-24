"""Public integration catalog facade."""

from __future__ import annotations

from typing import Any

from app.integrations import _catalog_impl
from app.integrations.store import load_integrations


def _sync_overrides() -> None:
    """Keep monkeypatch-friendly facade attributes wired into the implementation module."""
    _catalog_impl.load_integrations = load_integrations


def classify_integrations(integrations: list[dict[str, Any]]) -> dict[str, Any]:
    _sync_overrides()
    return _catalog_impl.classify_integrations(integrations)


def load_env_integrations() -> list[dict[str, Any]]:
    _sync_overrides()
    return _catalog_impl.load_env_integrations()


def merge_local_integrations(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _sync_overrides()
    return _catalog_impl.merge_local_integrations(store_integrations, env_integrations)


def merge_integrations_by_service(
    *integration_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _sync_overrides()
    return _catalog_impl.merge_integrations_by_service(*integration_groups)


def resolve_effective_integrations(
    store_integrations: list[dict[str, Any]] | None = None,
    env_integrations: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    _sync_overrides()
    return _catalog_impl.resolve_effective_integrations(
        store_integrations=store_integrations,
        env_integrations=env_integrations,
    )


def configured_integration_services() -> list[str]:
    """Return lowercase service keys for integrations configured via env or the local store.

    Single source of truth shared by the welcome banner and the REPL session so
    they never disagree about which integrations are connected. Covers both
    environment-variable configuration and integrations saved to ``~/.opensre``
    (e.g. via ``opensre integrations setup ...`` or the first-launch GitHub
    login). Never raises; returns an empty list on any failure so callers can
    treat it as best-effort.
    """
    services: list[str] = []

    try:
        env_records = load_env_integrations()
    except Exception:
        env_records = []
    for record in env_records:
        service = str(record.get("service", "")).strip().lower()
        if service:
            services.append(service)

    try:
        store_records = load_integrations()
    except Exception:
        store_records = []
    for record in store_records:
        if str(record.get("status", "active")).strip().lower() != "active":
            continue
        service = str(record.get("service", "")).strip().lower()
        if service:
            services.append(service)

    return list(dict.fromkeys(services))  # deduplicate, preserve order


# Hosted MCP integrations that strictly require a personal API token when not
# running in ``stdio`` mode. A record carrying only a URL classifies as present
# but cannot connect, so callers must not imply it is working.
_HOSTED_MCP_TOKEN_REQUIRED: frozenset[str] = frozenset({"posthog_mcp", "sentry_mcp"})


def _hosted_mcp_missing_token(service: str, config: dict[str, Any]) -> bool:
    """Offline check for an obviously-unusable hosted MCP config.

    Hosted MCP servers (non-``stdio``) authenticate with a personal API token;
    a record with only a URL is "configured" but cannot connect. Mirrors the
    runtime-unavailable checks in the MCP integration modules without doing any
    network I/O.
    """
    if service not in _HOSTED_MCP_TOKEN_REQUIRED:
        return False
    if str(config.get("mode", "")).strip().lower() == "stdio":
        return False
    return not str(config.get("auth_token", "")).strip()


def configured_integration_health() -> list[tuple[str, str]]:
    """Return ``(service, status)`` for each configured integration.

    ``status`` is ``"ok"`` when the stored/env config is minimally complete
    enough to attempt a connection, or ``"incomplete"`` when required
    credentials are missing — for example a hosted MCP record saved without an
    API token, or a service whose secrets did not classify into a usable config.
    The welcome banner uses this so it reflects health rather than mere presence.

    Performs no network verification (startup stays fast) and never raises; on
    any failure each service falls back to ``"ok"`` so the banner still lists it.
    """
    services = configured_integration_services()
    if not services:
        return []

    try:
        effective = resolve_effective_integrations()
    except Exception:
        # Health can't be determined offline; list everything without alarming.
        return [(service, "ok") for service in services]

    health: list[tuple[str, str]] = []
    for service in services:
        entry = effective.get(service)
        if entry is None:
            # Present in the store/env but its credentials did not classify into
            # a usable config (a required secret is missing).
            health.append((service, "incomplete"))
            continue
        config = entry.get("config") if isinstance(entry, dict) else None
        if isinstance(config, dict) and _hosted_mcp_missing_token(service, config):
            health.append((service, "incomplete"))
            continue
        health.append((service, "ok"))
    return health


__all__ = [
    "classify_integrations",
    "configured_integration_health",
    "configured_integration_services",
    "load_env_integrations",
    "load_integrations",
    "merge_integrations_by_service",
    "merge_local_integrations",
    "resolve_effective_integrations",
]
