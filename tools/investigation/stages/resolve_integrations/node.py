"""Resolve integrations node — discovers which integrations are available for this alert."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from context.state import InvestigationState
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
from platform.observability import get_progress_tracker as get_tracker

logger = logging.getLogger(__name__)


def resolve_integrations(state: InvestigationState) -> dict[str, Any]:
    """Discover and classify all integrations available for this investigation.

    Reads  : _auth_token, org_id, resolved_integrations (idempotency guard)
    Writes : resolved_integrations
    """
    return {"resolved_integrations": _resolve(state, emit_progress=True)}


def resolve_integrations_quiet(state: InvestigationState) -> dict[str, Any]:
    """Like :func:`resolve_integrations` but without progress-tracker UI."""
    return _resolve(state, emit_progress=False)


def _resolve(state: InvestigationState, *, emit_progress: bool) -> dict[str, Any]:
    """Return the raw integrations dict (keyed by vendor name)."""
    if state.get("resolved_integrations"):
        return dict(state["resolved_integrations"])

    tracker = get_tracker() if emit_progress else None
    if tracker is not None:
        tracker.start("resolve_integrations", "Fetching org integrations")

    org_id = state.get("org_id", "")
    auth_token = _strip_bearer((state.get("_auth_token", "") or "").strip())

    if auth_token:
        if not org_id:
            org_id = _decode_org_id_from_token(auth_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            _complete_tracker(
                tracker,
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
            )
            return {}
        try:
            from integrations.port import fetch_remote_integrations

            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=auth_token)
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            _complete_tracker(
                tracker,
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
            )
            return {}
        resolved = _classify_integrations(all_integrations)
        _log_resolved(tracker, resolved)
        return resolved

    env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
    if env_token:
        if not org_id:
            org_id = _decode_org_id_from_token(env_token)
        if not org_id:
            return _resolve_from_local_sources(tracker)
        try:
            from integrations.port import fetch_remote_integrations

            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_local_sources(tracker)
        return _resolve_remote_with_local_fallback(all_integrations, tracker)

    return _resolve_from_local_sources(tracker)


def _complete_tracker(tracker: Any | None, node_name: str, **kwargs: Any) -> None:
    if tracker is not None:
        tracker.complete(node_name, **kwargs)


def _log_resolved(tracker: Any | None, resolved: dict[str, Any]) -> None:
    services = [s for s in resolved if s != "_all"]
    _complete_tracker(
        tracker,
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved integrations: {services}"
        if services
        else "No active integrations found",
    )


def _resolve_from_local_sources(tracker: Any | None) -> dict[str, Any]:
    from integrations.store import STORE_PATH, load_integrations

    store_integrations = load_integrations()
    env_integrations = _load_env_integrations() if not store_integrations else []
    integrations = _merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        _complete_tracker(
            tracker,
            "resolve_integrations",
            fields_updated=["resolved_integrations"],
            message=(
                f"No auth context and no local integrations found "
                f"(store: {STORE_PATH}, env fallback checked)"
            ),
        )
        return {}

    resolved = _classify_integrations(integrations)
    services = [s for s in resolved if s != "_all"]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    _complete_tracker(
        tracker,
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=(
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )
    return resolved


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
    tracker: Any | None,
) -> dict[str, Any]:
    from integrations.store import load_integrations

    store_integrations = load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [s for s in resolved if s != "_all"]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    _complete_tracker(
        tracker,
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=(
            f"Resolved integrations from {', '.join(source_labels)}: {services}"
            if services
            else "No active integrations found"
        ),
    )
    return resolved


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
