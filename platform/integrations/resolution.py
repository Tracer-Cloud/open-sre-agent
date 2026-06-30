"""Shared integration resolution for runtime consumers."""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from integrations.catalog import (
    classify_integrations as _classify_integrations,
)
from integrations.catalog import (
    load_env_integrations as _load_env_integrations,
)
from integrations.catalog import (
    merge_integrations_by_service as _merge_integrations_by_service,
)
from integrations.catalog import (
    merge_local_integrations as _merge_local_integrations,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntegrationResolutionResult:
    """Resolved integration configs plus optional user-visible progress text."""

    resolved_integrations: dict[str, Any]
    progress_message: str | None = None

    @property
    def services(self) -> tuple[str, ...]:
        """Resolved service names, excluding internal aggregate keys."""
        return tuple(service for service in self.resolved_integrations if service != "_all")


def resolve_integrations(state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return integration configs keyed by service for any runtime consumer."""
    return resolve_integrations_with_metadata(state).resolved_integrations


def resolve_integrations_with_metadata(
    state: Mapping[str, Any] | None = None,
) -> IntegrationResolutionResult:
    """Discover and classify all integrations available to the current runtime.

    ``state`` may include ``resolved_integrations`` for idempotency, ``_auth_token``
    for remote lookup, and ``org_id`` to avoid decoding a JWT. The resolver is
    intentionally independent of investigation state models so shell, SDK,
    gateway, and investigation consumers can share the same behavior.
    """
    state_map = state or {}
    existing = state_map.get("resolved_integrations")
    if existing:
        return IntegrationResolutionResult(dict(existing))

    org_id = str(state_map.get("org_id") or "")
    auth_token = _strip_bearer(str(state_map.get("_auth_token") or "").strip())

    if auth_token:
        if not org_id:
            org_id = _decode_org_id_from_token(auth_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            return IntegrationResolutionResult({})
        try:
            from integrations.port import fetch_remote_integrations

            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=auth_token)
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            return IntegrationResolutionResult({})
        resolved = _classify_integrations(all_integrations)
        return IntegrationResolutionResult(resolved, _resolved_message(resolved))

    env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
    if env_token:
        if not org_id:
            org_id = _decode_org_id_from_token(env_token)
        if not org_id:
            return _resolve_from_local_sources()
        try:
            from integrations.port import fetch_remote_integrations

            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_local_sources()
        return _resolve_remote_with_local_fallback(all_integrations)

    return _resolve_from_local_sources()


def _resolved_message(resolved: dict[str, Any]) -> str:
    services = [service for service in resolved if service != "_all"]
    return f"Resolved integrations: {services}" if services else "No active integrations found"


def _resolve_from_local_sources() -> IntegrationResolutionResult:
    from integrations.store import STORE_PATH, load_integrations

    store_integrations = load_integrations()
    env_integrations = _load_env_integrations() if not store_integrations else []
    integrations = _merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        return IntegrationResolutionResult(
            {},
            (
                f"No auth context and no local integrations found "
                f"(store: {STORE_PATH}, env fallback checked)"
            ),
        )

    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if service != "_all"]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    return IntegrationResolutionResult(
        resolved,
        (
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
) -> IntegrationResolutionResult:
    from integrations.store import load_integrations

    store_integrations = load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [service for service in resolved if service != "_all"]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    return IntegrationResolutionResult(
        resolved,
        (
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


__all__ = [
    "IntegrationResolutionResult",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
]
