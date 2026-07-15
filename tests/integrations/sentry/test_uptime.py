"""Unit tests for Sentry uptime watch helpers (#4032)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from integrations.sentry import SentryConfig
from integrations.sentry.uptime import (
    UptimeMonitor,
    detect_uptime_transitions,
    format_uptime_transition_message,
    health_snapshot,
    list_sentry_uptime_monitors,
    load_watch_state,
    normalize_uptime_monitor,
    run_uptime_watch_tick,
    save_watch_state,
)


def _monitor(
    monitor_id: str,
    *,
    health: str = "up",
    url: str = "https://example.com",
    name: str = "example",
) -> UptimeMonitor:
    status = 2 if health == "down" else 1
    return UptimeMonitor(
        id=monitor_id,
        name=name,
        url=url,
        project_slug="tracer-30",
        health=health,  # type: ignore[arg-type]
        uptime_status=status,
        status="active",
    )


def test_normalize_uptime_monitor_maps_failed_status() -> None:
    monitor = normalize_uptime_monitor(
        {
            "id": "99",
            "name": "docs",
            "url": "https://docs.example.com",
            "projectSlug": "web",
            "uptimeStatus": 2,
            "status": "active",
        }
    )
    assert monitor is not None
    assert monitor.health == "down"
    assert monitor.project_slug == "web"


def test_detect_transitions_initial_down_and_recovery() -> None:
    down = _monitor("1", health="down")
    up = _monitor("1", health="up")

    initial = detect_uptime_transitions({}, [down], notify_initial_down=True)
    assert len(initial) == 1
    assert initial[0].kind == "down"

    recovered = detect_uptime_transitions({"1": "down"}, [up])
    assert len(recovered) == 1
    assert recovered[0].kind == "recovered"

    quiet = detect_uptime_transitions({"1": "up"}, [up])
    assert quiet == []


def test_format_message_marks_critical_downtime() -> None:
    transitions = detect_uptime_transitions({}, [_monitor("1", health="down", name="api")])
    message = format_uptime_transition_message(transitions)
    assert "CRITICAL downtime" in message
    assert "api" in message
    assert format_uptime_transition_message([]) == ""


def test_watch_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_watch_state("task-a", {"1": "down", "2": "up"}, path=path)
    assert load_watch_state("task-a", path=path) == {"1": "down", "2": "up"}
    assert load_watch_state("missing", path=path) == {}


def test_list_sentry_uptime_monitors_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_request(
        config: SentryConfig,
        method: str,
        path: str,
        *,
        params: list | None = None,
    ) -> list[dict]:
        assert method == "GET"
        assert path.endswith("/uptime/")
        assert ("project", "web") in (params or [])
        return [
            {
                "id": "7",
                "name": "homepage",
                "url": "https://example.com",
                "projectSlug": "web",
                "uptimeStatus": 1,
                "status": "active",
            }
        ]

    monkeypatch.setattr("integrations.sentry.uptime._request_json", _fake_request)
    config = SentryConfig(
        organization_slug="acme",
        auth_token="token",
        project_slug="web",
    )
    monitors = list_sentry_uptime_monitors(config=config)
    assert len(monitors) == 1
    assert monitors[0].health == "up"
    assert health_snapshot(monitors) == {"7": "up"}


def test_list_sentry_uptime_monitors_403_includes_alerts_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://sentry.io/api/0/organizations/acme/uptime/")
    response = httpx.Response(403, request=request, text='{"detail":"forbidden"}')

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    monkeypatch.setattr("integrations.sentry.uptime._request_json", _raise)
    config = SentryConfig(organization_slug="acme", auth_token="token")
    with pytest.raises(RuntimeError, match="alerts:read"):
        list_sentry_uptime_monitors(config=config)


def test_run_uptime_watch_tick_notifies_then_quiets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    config = SentryConfig(organization_slug="acme", auth_token="token")
    monkeypatch.setattr(
        "integrations.sentry.uptime.resolve_sentry_config",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(
        "integrations.sentry.uptime.list_sentry_uptime_monitors",
        lambda **_kwargs: [_monitor("1", health="down", name="api")],
    )

    first = run_uptime_watch_tick(task_id="t1", state_path=state_path)
    assert "CRITICAL downtime" in first
    second = run_uptime_watch_tick(task_id="t1", state_path=state_path)
    assert second == ""
