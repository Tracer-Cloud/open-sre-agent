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
    """Return lowercase service keys for integrations configured via env vars.

    Single source of truth shared by the welcome banner and the REPL session so
    they never disagree about which integrations are connected. Never raises;
    returns an empty list on any failure so callers can treat it as best-effort.
    """
    try:
        records = load_env_integrations()
    except Exception:
        return []
    services: list[str] = []
    for record in records:
        service = str(record.get("service", "")).strip().lower()
        if service:
            services.append(service)
    return list(dict.fromkeys(services))  # deduplicate, preserve order


__all__ = [
    "classify_integrations",
    "configured_integration_services",
    "load_env_integrations",
    "load_integrations",
    "merge_integrations_by_service",
    "merge_local_integrations",
    "resolve_effective_integrations",
]
