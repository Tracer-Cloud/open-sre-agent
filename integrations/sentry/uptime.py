"""Sentry uptime monitor polling, state transitions, and notify messages.

v1 for #4032: REST poll of organization uptime monitors + DOWN/RECOVERED
transition messages. No skill, no remediation, no morning-report section.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from config.constants import OPENSRE_HOME_DIR
from integrations.sentry import (
    SentryConfig,
    _request_json,
    build_sentry_config,
    describe_sentry_api_error,
    sentry_config_from_env,
)
from integrations.store import get_integration

logger = logging.getLogger(__name__)

# Sentry UptimeStatus IntEnum: OK=1, FAILED=2
_UPTIME_STATUS_OK = 1
_UPTIME_STATUS_FAILED = 2

MonitorHealth = Literal["up", "down", "unknown"]
TransitionKind = Literal["down", "recovered"]

_ALERTS_READ_HINT = (
    "Uptime monitor listing requires a Sentry auth token with alerts:read "
    "(or org:read). The Issues-only event:read scope is not enough — update "
    "the token scopes and re-run `opensre integrations setup` / verify sentry."
)


@dataclass(frozen=True)
class UptimeMonitor:
    """Normalized Sentry uptime monitor row."""

    id: str
    name: str
    url: str
    project_slug: str
    health: MonitorHealth
    uptime_status: int | None
    status: str


@dataclass(frozen=True)
class UptimeTransition:
    """A health transition worth notifying about."""

    kind: TransitionKind
    monitor: UptimeMonitor


def resolve_sentry_config(*, project_slug: str = "") -> SentryConfig | None:
    """Resolve Sentry REST config from env, then the integration store."""
    env_config = sentry_config_from_env()
    store = get_integration("sentry") or {}
    store_config = store.get("config") if isinstance(store.get("config"), dict) else store

    organization_slug = (env_config.organization_slug if env_config else "") or str(
        store_config.get("organization_slug") or ""
    ).strip()
    auth_token = (env_config.auth_token if env_config else "") or str(
        store_config.get("auth_token") or ""
    ).strip()
    if not organization_slug or not auth_token:
        return None

    slug = (
        project_slug.strip()
        or (env_config.project_slug if env_config else "")
        or str(store_config.get("project_slug") or "").strip()
    )
    return build_sentry_config(
        {
            "base_url": (
                (env_config.base_url if env_config else "")
                or str(store_config.get("base_url") or "https://sentry.io").strip()
                or "https://sentry.io"
            ),
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": slug,
        }
    )


def _normalize_health(uptime_status: Any) -> MonitorHealth:
    try:
        value = int(uptime_status)
    except (TypeError, ValueError):
        return "unknown"
    if value == _UPTIME_STATUS_FAILED:
        return "down"
    if value == _UPTIME_STATUS_OK:
        return "up"
    return "unknown"


def normalize_uptime_monitor(raw: dict[str, Any]) -> UptimeMonitor | None:
    """Normalize one Sentry uptime detector payload."""
    monitor_id = str(raw.get("id") or "").strip()
    if not monitor_id:
        return None
    uptime_status = raw.get("uptimeStatus", raw.get("uptime_status"))
    return UptimeMonitor(
        id=monitor_id,
        name=str(raw.get("name") or "").strip() or f"uptime-{monitor_id}",
        url=str(raw.get("url") or "").strip(),
        project_slug=str(raw.get("projectSlug") or raw.get("project_slug") or "").strip(),
        health=_normalize_health(uptime_status),
        uptime_status=(
            int(uptime_status)
            if isinstance(uptime_status, int)
            or (isinstance(uptime_status, str) and uptime_status.isdigit())
            else None
        ),
        status=str(raw.get("status") or "").strip() or "unknown",
    )


def list_sentry_uptime_monitors(*, config: SentryConfig) -> list[UptimeMonitor]:
    """List organization uptime monitors via REST.

    Endpoint: ``GET /api/0/organizations/{org}/uptime/``
    """
    path = f"/api/0/organizations/{config.organization_slug}/uptime/"
    params: list[tuple[str, str | int | float | bool | None]] = []
    if config.project_slug:
        params.append(("project", config.project_slug))

    try:
        payload = _request_json(config, "GET", path, params=params or None)
    except httpx.HTTPStatusError as err:
        detail = describe_sentry_api_error(
            err,
            project_slug=config.project_slug,
        )
        if err.response.status_code == 403:
            detail = f"{detail} {_ALERTS_READ_HINT}"
        raise RuntimeError(detail) from err

    rows = payload if isinstance(payload, list) else []
    monitors: list[UptimeMonitor] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = normalize_uptime_monitor(row)
        if normalized is not None:
            monitors.append(normalized)
    return monitors


def health_snapshot(monitors: list[UptimeMonitor]) -> dict[str, MonitorHealth]:
    """Map monitor id → health for persistence."""
    return {monitor.id: monitor.health for monitor in monitors}


def detect_uptime_transitions(
    previous: dict[str, MonitorHealth],
    monitors: list[UptimeMonitor],
    *,
    notify_initial_down: bool = True,
) -> list[UptimeTransition]:
    """Return DOWN / RECOVERED transitions versus *previous* snapshot.

    First poll (empty *previous*): optionally emit DOWN for currently-failed
    monitors so existing outages are not silent until the next flip.
    """
    transitions: list[UptimeTransition] = []
    by_id = {monitor.id: monitor for monitor in monitors}

    if not previous:
        if notify_initial_down:
            for monitor in monitors:
                if monitor.health == "down":
                    transitions.append(UptimeTransition(kind="down", monitor=monitor))
        return transitions

    for monitor_id, monitor in by_id.items():
        prior = previous.get(monitor_id)
        if prior is None:
            if monitor.health == "down":
                transitions.append(UptimeTransition(kind="down", monitor=monitor))
            continue
        if prior != "down" and monitor.health == "down":
            transitions.append(UptimeTransition(kind="down", monitor=monitor))
        elif prior == "down" and monitor.health == "up":
            transitions.append(UptimeTransition(kind="recovered", monitor=monitor))

    for monitor_id, prior in previous.items():
        if monitor_id in by_id:
            continue
        # Monitor disappeared from the API — if it was down, treat as recovered
        # only when we no longer observe it as failing (cannot confirm up).
        if prior == "down":
            logger.info("Previously down uptime monitor %s no longer listed", monitor_id)

    return transitions


def format_uptime_transition_message(transitions: list[UptimeTransition]) -> str:
    """Format a plain-text notify body for delivery providers."""
    if not transitions:
        return ""

    lines = ["Sentry uptime watch"]
    for transition in transitions:
        monitor = transition.monitor
        target = monitor.url or monitor.name
        project = f" [{monitor.project_slug}]" if monitor.project_slug else ""
        if transition.kind == "down":
            lines.append(f"CRITICAL downtime{project}: {monitor.name} — {target}")
        else:
            lines.append(f"RECOVERED{project}: {monitor.name} — {target}")
    return "\n".join(lines)


def _state_path() -> Path:
    return OPENSRE_HOME_DIR / "sentry_uptime_watch_state.json"


def load_watch_state(
    task_id: str,
    *,
    path: Path | None = None,
) -> dict[str, MonitorHealth]:
    """Load prior health snapshot for a scheduled watch task."""
    store_path = path or _state_path()
    if not store_path.exists():
        return {}
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read uptime watch state: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    entry = raw.get(task_id)
    if not isinstance(entry, dict):
        return {}
    snapshot = entry.get("health")
    if not isinstance(snapshot, dict):
        return {}
    out: dict[str, MonitorHealth] = {}
    for key, value in snapshot.items():
        if value in ("up", "down", "unknown"):
            out[str(key)] = value  # type: ignore[assignment]
    return out


def save_watch_state(
    task_id: str,
    health: dict[str, MonitorHealth],
    *,
    path: Path | None = None,
) -> None:
    """Persist health snapshot for a scheduled watch task."""
    store_path = path or _state_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if store_path.exists():
        try:
            loaded = json.loads(store_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing[task_id] = {"health": health}
    store_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_uptime_watch_tick(
    *,
    task_id: str,
    project_slug: str = "",
    state_path: Path | None = None,
    notify_initial_down: bool = True,
) -> str:
    """Poll uptime monitors and return a notify message (empty if quiet)."""
    config = resolve_sentry_config(project_slug=project_slug)
    if config is None:
        raise RuntimeError(
            "Sentry is not configured. Run `opensre integrations setup` and verify "
            "with `opensre integrations verify sentry`."
        )

    monitors = list_sentry_uptime_monitors(config=config)
    previous = load_watch_state(task_id, path=state_path)
    transitions = detect_uptime_transitions(
        previous,
        monitors,
        notify_initial_down=notify_initial_down,
    )
    save_watch_state(task_id, health_snapshot(monitors), path=state_path)
    return format_uptime_transition_message(transitions)


__all__ = [
    "UptimeMonitor",
    "UptimeTransition",
    "detect_uptime_transitions",
    "format_uptime_transition_message",
    "health_snapshot",
    "list_sentry_uptime_monitors",
    "load_watch_state",
    "normalize_uptime_monitor",
    "resolve_sentry_config",
    "run_uptime_watch_tick",
    "save_watch_state",
]
