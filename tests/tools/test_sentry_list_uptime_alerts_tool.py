"""Tests for list_sentry_uptime_alerts tool."""

from __future__ import annotations

from integrations.sentry.tools.sentry_list_uptime_alerts_tool import list_sentry_uptime_alerts
from integrations.sentry.uptime import UptimeMonitor


def test_list_sentry_uptime_alerts_returns_normalized_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool._resolve_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool.list_sentry_uptime_monitors",
        lambda **_kwargs: [
            UptimeMonitor(
                id="1",
                name="api",
                url="https://api.example.com",
                project_slug="web",
                health="down",
                uptime_status=2,
                status="active",
            )
        ],
    )

    result = list_sentry_uptime_alerts(organization_slug="acme", sentry_token="tok")
    assert result["available"] is True
    assert result["down_count"] == 1
    assert result["monitors"][0]["severity"] == "critical"
    assert result["monitors"][0]["health"] == "down"


def test_list_sentry_uptime_alerts_missing_creds(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool.sentry_config_from_env",
        lambda: None,
    )
    result = list_sentry_uptime_alerts(organization_slug="", sentry_token="")
    assert result["available"] is False
    assert "error" in result
