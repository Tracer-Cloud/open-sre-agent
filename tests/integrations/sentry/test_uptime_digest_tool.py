"""Tests for the get_sentry_uptime_digest tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from integrations.sentry.tools.sentry_uptime_digest_tool import (
    get_sentry_uptime_digest,
)
from integrations.sentry.uptime import (
    UptimeTransitionRecord,
    WatchState,
)


def _uptime_record(
    monitor_id: str,
    *,
    kind: str = "down",
    at: datetime,
    name: str = "example",
    url: str = "https://example.com",
    project_slug: str = "web",
) -> UptimeTransitionRecord:
    return UptimeTransitionRecord(
        monitor_id=monitor_id,
        kind=kind,  # type: ignore[arg-type]
        at=at.isoformat(),
        name=name,
        url=url,
        project_slug=project_slug,
    )


class TestGetSentryUptimeDigest:
    def test_no_watch_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.load_all_watch_states",
            lambda **_kw: {},
        )
        result = get_sentry_uptime_digest()
        assert result["uptime_watch_active"] is False
        assert result["window_hours"] == 24
        assert result["still_down_count"] == 0
        assert result["recovered_count"] == 0
        assert result["still_down"] == []
        assert result["recovered"] == []

    def test_still_down_monitor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
        down_at = now - timedelta(hours=6)
        states = {
            "task-a": WatchState(
                open_incidents={"1"},
                transitions=[
                    _uptime_record("1", at=down_at, url="https://sandbox.tracer.cloud"),
                ],
            )
        }
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.load_all_watch_states",
            lambda **_kw: states,
        )
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.datetime",
            type("_MockDT", (), {"now": staticmethod(lambda _tz: now)}),
        )
        result = get_sentry_uptime_digest()
        assert result["uptime_watch_active"] is True
        assert result["still_down_count"] == 1
        assert result["still_down"][0]["monitor"] == "sandbox.tracer.cloud"

    def test_recovered_monitor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
        states = {
            "task-a": WatchState(
                transitions=[
                    _uptime_record(
                        "1",
                        kind="down",
                        at=now - timedelta(hours=5),
                        url="https://api.example.com",
                    ),
                    _uptime_record(
                        "1",
                        kind="recovered",
                        at=now - timedelta(hours=1),
                        url="https://api.example.com",
                    ),
                ]
            )
        }
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.load_all_watch_states",
            lambda **_kw: states,
        )
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.datetime",
            type("_MockDT", (), {"now": staticmethod(lambda _tz: now)}),
        )
        result = get_sentry_uptime_digest()
        assert result["recovered_count"] == 1
        assert result["recovered"][0]["monitor"] == "api.example.com"

    def test_no_incidents_in_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
        states = {
            "task-a": WatchState(
                transitions=[
                    _uptime_record(
                        "1",
                        kind="recovered",
                        at=now - timedelta(days=2),
                        url="https://old.example.com",
                    )
                ]
            )
        }
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.load_all_watch_states",
            lambda **_kw: states,
        )
        monkeypatch.setattr(
            "integrations.sentry.tools.sentry_uptime_digest_tool.datetime",
            type("_MockDT", (), {"now": staticmethod(lambda _tz: now)}),
        )
        result = get_sentry_uptime_digest()
        assert result["uptime_watch_active"] is True
        assert "No uptime incidents" in result.get("message", "")
