"""Tests for asyncio.gather CancelledError bug in DatadogAsyncClient.fetch_all."""

from __future__ import annotations

import asyncio
from typing import Any

from integrations.datadog.client import DatadogAsyncClient
from integrations.config_models import DatadogIntegrationConfig

_DD_CONFIG = DatadogIntegrationConfig(
    api_key="fake-api-key",
    app_key="fake-app-key",
)

_LOGS_OK: dict[str, Any] = {"success": True, "logs": [{"message": "log1"}], "total": 1, "duration_ms": 10}
_MONITORS_OK: dict[str, Any] = {"success": True, "monitors": [{"name": "CPU Monitor"}], "total": 1, "duration_ms": 10}
_EVENTS_OK: dict[str, Any] = {"success": True, "events": [{"title": "deploy"}], "total": 1, "duration_ms": 10}


async def _logs_ok(client: Any, *a: Any, **kw: Any) -> dict[str, Any]:
    return _LOGS_OK


async def _monitors_ok(client: Any, *a: Any, **kw: Any) -> dict[str, Any]:
    return _MONITORS_OK


async def _events_ok(client: Any, *a: Any, **kw: Any) -> dict[str, Any]:
    return _EVENTS_OK


async def _events_cancelled(client: Any, *a: Any, **kw: Any) -> dict[str, Any]:
    raise asyncio.CancelledError("upstream timeout signal")


def test_fetch_all_partial_results_survive_cancelled_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When _get_events raises CancelledError, logs and monitors still come through."""
    dd = DatadogAsyncClient(config=_DD_CONFIG)
    monkeypatch.setattr(dd, "_search_logs", _logs_ok)
    monkeypatch.setattr(dd, "_list_monitors", _monitors_ok)
    monkeypatch.setattr(dd, "_get_events", _events_cancelled)

    result = asyncio.run(
        dd.fetch_all(
            logs_query="*",
            time_range_minutes=60,
            logs_limit=50,
            monitor_query=None,
            events_query=None,
        )
    )

    assert result["logs"]["success"] is True
    assert result["monitors"]["success"] is True
    assert result["events"]["success"] is False
    assert result["events"].get("source") == "events"


def test_fetch_all_returns_all_results_when_all_succeed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Normal path — all three fetches succeed and all results returned."""
    dd = DatadogAsyncClient(config=_DD_CONFIG)
    monkeypatch.setattr(dd, "_search_logs", _logs_ok)
    monkeypatch.setattr(dd, "_list_monitors", _monitors_ok)
    monkeypatch.setattr(dd, "_get_events", _events_ok)

    result = asyncio.run(
        dd.fetch_all(
            logs_query="*",
            time_range_minutes=60,
            logs_limit=50,
            monitor_query=None,
            events_query=None,
        )
    )

    assert result["logs"]["success"] is True
    assert result["monitors"]["success"] is True
    assert result["events"]["success"] is True