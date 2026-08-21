"""Integration resolution and the surface setup command.

Core resolves which integrations are active (from a remote org vault, the local
store, or env) without importing the integration store/catalog packages, and
renders an upgrade CTA without knowing a surface's slash syntax. The adapters
and the setup-command renderer are injected at boot.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

RemoteIntegrationsFetcher = Callable[[str, str], list[dict[str, Any]]]
LoadIntegrationsFn = Callable[[], list[dict[str, Any]]]
IntegrationStorePathFn = Callable[[], str]
LoadEnvIntegrationsFn = Callable[[], list[dict[str, Any]]]
WebappVaultFetcherFn = Callable[[], list[dict[str, Any]] | None]
ClassifyIntegrationsFn = Callable[[list[dict[str, Any]]], dict[str, Any]]
MergeLocalIntegrationsFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]
]
MergeIntegrationsByServiceFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    list[dict[str, Any]],
]
ConfiguredIntegrationServicesFn = Callable[[], tuple[str, ...]]
SetupableIntegrationServicesFn = Callable[[], tuple[str, ...]]


def _default_fetch_remote(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    _ = (org_id, auth_token)
    return []


def _default_load_integrations() -> list[dict[str, Any]]:
    return []


def _default_store_path() -> str:
    return ""


def _default_load_env_integrations() -> list[dict[str, Any]]:
    return []


def _default_classify_integrations(_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {}


def _default_merge_local(
    store: list[dict[str, Any]], env: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [*store, *env]


def _default_merge_by_service(
    env: list[dict[str, Any]],
    store: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [*env, *store, *remote]


def _default_configured_services() -> tuple[str, ...]:
    return ()


def _default_setupable_services() -> tuple[str, ...]:
    return ()


def _default_fetch_webapp_vault() -> list[dict[str, Any]] | None:
    return None


_fetch_remote: RemoteIntegrationsFetcher = _default_fetch_remote
_load_integrations: LoadIntegrationsFn = _default_load_integrations
_store_path: IntegrationStorePathFn = _default_store_path
_load_env_integrations: LoadEnvIntegrationsFn = _default_load_env_integrations
_classify_integrations: ClassifyIntegrationsFn = _default_classify_integrations
_merge_local_integrations: MergeLocalIntegrationsFn = _default_merge_local
_merge_integrations_by_service: MergeIntegrationsByServiceFn = _default_merge_by_service
_configured_integration_services: ConfiguredIntegrationServicesFn = _default_configured_services
_setupable_integration_services: SetupableIntegrationServicesFn = _default_setupable_services
_fetch_webapp_vault: WebappVaultFetcherFn = _default_fetch_webapp_vault


def set_remote_integrations_fetcher(fetcher: RemoteIntegrationsFetcher) -> None:
    global _fetch_remote
    _fetch_remote = fetcher


def fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
    return _fetch_remote(org_id, auth_token)


def configured_integration_services() -> tuple[str, ...]:
    return _configured_integration_services()


def set_setupable_integration_services(fetcher: SetupableIntegrationServicesFn) -> None:
    """Register the catalog of service ids valid for ``/integrations setup``."""
    global _setupable_integration_services
    _setupable_integration_services = fetcher


def setupable_integration_services() -> tuple[str, ...]:
    """Service ids that have a real setup handler (never invent outside this set)."""
    return _setupable_integration_services()


def set_integration_resolution_adapters(
    *,
    load_integrations: LoadIntegrationsFn | None = None,
    integration_store_path: IntegrationStorePathFn | None = None,
    load_env_integrations: LoadEnvIntegrationsFn | None = None,
    classify_integrations: ClassifyIntegrationsFn | None = None,
    merge_local_integrations: MergeLocalIntegrationsFn | None = None,
    merge_integrations_by_service: MergeIntegrationsByServiceFn | None = None,
    configured_services: ConfiguredIntegrationServicesFn | None = None,
    fetch_webapp_vault: WebappVaultFetcherFn | None = None,
) -> None:
    global _load_integrations, _store_path, _load_env_integrations
    global _classify_integrations, _merge_local_integrations
    global _merge_integrations_by_service, _configured_integration_services
    global _fetch_webapp_vault
    if load_integrations is not None:
        _load_integrations = load_integrations
    if integration_store_path is not None:
        _store_path = integration_store_path
    if load_env_integrations is not None:
        _load_env_integrations = load_env_integrations
    if classify_integrations is not None:
        _classify_integrations = classify_integrations
    if merge_local_integrations is not None:
        _merge_local_integrations = merge_local_integrations
    if merge_integrations_by_service is not None:
        _merge_integrations_by_service = merge_integrations_by_service
    if configured_services is not None:
        _configured_integration_services = configured_services
    if fetch_webapp_vault is not None:
        _fetch_webapp_vault = fetch_webapp_vault


class IntegrationResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    resolved_integrations: dict[str, Any] | None = None
    auth_token: str = Field(default="", alias="_auth_token")
    org_id: str = ""

    @field_validator("auth_token", "org_id", mode="before")
    @classmethod
    def _coerce_optional_string(cls, value: Any) -> str:
        return str(value or "").strip()


class IntegrationResolutionResult(StrictConfigModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_integrations: dict[str, Any] = Field(default_factory=dict)
    progress_message: str | None = None

    @property
    def services(self) -> tuple[str, ...]:
        return tuple(
            service for service in self.resolved_integrations if not service.startswith("_")
        )


def resolve_integrations(state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return resolve_integrations_with_metadata(state).resolved_integrations


def resolve_integrations_with_metadata(
    state: Mapping[str, Any] | None = None,
) -> IntegrationResolutionResult:
    request = IntegrationResolutionRequest.model_validate(state or {})
    existing = request.resolved_integrations
    if existing:
        return IntegrationResolutionResult(resolved_integrations=dict(existing))

    org_id = request.org_id
    auth_token = _strip_bearer(request.auth_token)

    if auth_token:
        if not org_id:
            org_id = _decode_org_id_from_token(auth_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            return IntegrationResolutionResult()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=auth_token)
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            return IntegrationResolutionResult()
        resolved = _classify_integrations(all_integrations)
        return IntegrationResolutionResult(
            resolved_integrations=resolved,
            progress_message=_resolved_message(resolved),
        )

    env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
    if env_token:
        if not org_id:
            org_id = _decode_org_id_from_token(env_token)
        if not org_id:
            return _resolve_from_webapp_vault_or_local()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_webapp_vault_or_local()
        return _resolve_remote_with_local_fallback(all_integrations)

    return _resolve_from_webapp_vault_or_local()


def _resolve_from_webapp_vault_or_local() -> IntegrationResolutionResult:
    """Silo path: pull org vault from opensre-webapp, else local store/env.

    Merge order is vault → store → env so ops can still override a vault
    secret with ``GITHUB_MCP_AUTH_TOKEN`` (etc.) on the task definition.
    """
    remote = _fetch_webapp_vault()
    if remote is None:
        return _resolve_from_local_sources()
    if not remote:
        # Explicit empty vault — still allow local/env overlays (e.g. Slack SSM).
        return _resolve_from_local_sources()

    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        remote,
        store_integrations,
        env_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from webapp vault"
            f"{', store' if store_integrations else ''}"
            f"{', env' if env_integrations else ''}: {services}"
            if services
            else "No active integrations found"
        ),
    )


def _resolved_message(resolved: dict[str, Any]) -> str:
    services = [service for service in resolved if not service.startswith("_")]
    return f"Resolved integrations: {services}" if services else "No active integrations found"


def _resolve_from_local_sources() -> IntegrationResolutionResult:
    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations() if not store_integrations else []
    integrations = _merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        return IntegrationResolutionResult(
            resolved_integrations={},
            progress_message=(
                f"No auth context and no local integrations found "
                f"(store: {_store_path()}, env fallback checked)"
            ),
        )

    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
) -> IntegrationResolutionResult:
    store_integrations = _load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from {', '.join(source_labels)}: {services}"
            if services
            else "No active integrations found"
        ),
    )


def _decode_org_id_from_token(token: str) -> str:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("organization") or claims.get("org_id") or ""
    except Exception:
        logger.debug("Failed to decode org_id from JWT token", exc_info=True)
        return ""


def _strip_bearer(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token.split(None, 1)[1].strip()
    return token


# ── Integration setup command (surface syntax) ────────────────────────────
# Core builds the upgrade CTA but must not know slash syntax. The surface
# registers how it spells "connect this integration" at boot.


def _default_integration_setup_command(service_id: str) -> str:
    return f"integrations setup {service_id}"


_integration_setup_command: Callable[[str], str] = _default_integration_setup_command


def set_integration_setup_command(render: Callable[[str], str]) -> None:
    """Register how this surface spells the connect command for a service."""
    global _integration_setup_command
    _integration_setup_command = render


def integration_setup_command(service_id: str) -> str:
    """Return the surface command that connects ``service_id``."""
    return _integration_setup_command(service_id)


def reset() -> None:
    """Restore integration-resolution and setup-command defaults (tests)."""
    set_remote_integrations_fetcher(_default_fetch_remote)
    set_integration_resolution_adapters(
        load_integrations=_default_load_integrations,
        integration_store_path=_default_store_path,
        load_env_integrations=_default_load_env_integrations,
        classify_integrations=_default_classify_integrations,
        merge_local_integrations=_default_merge_local,
        merge_integrations_by_service=_default_merge_by_service,
        configured_services=_default_configured_services,
        fetch_webapp_vault=_default_fetch_webapp_vault,
    )
    set_integration_setup_command(_default_integration_setup_command)
    set_setupable_integration_services(_default_setupable_services)


__all__ = [
    "ClassifyIntegrationsFn",
    "ConfiguredIntegrationServicesFn",
    "IntegrationResolutionRequest",
    "IntegrationResolutionResult",
    "IntegrationStorePathFn",
    "LoadEnvIntegrationsFn",
    "LoadIntegrationsFn",
    "MergeIntegrationsByServiceFn",
    "MergeLocalIntegrationsFn",
    "RemoteIntegrationsFetcher",
    "SetupableIntegrationServicesFn",
    "WebappVaultFetcherFn",
    "configured_integration_services",
    "fetch_remote_integrations",
    "integration_setup_command",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
    "set_integration_resolution_adapters",
    "set_integration_setup_command",
    "set_remote_integrations_fetcher",
    "set_setupable_integration_services",
    "setupable_integration_services",
]
