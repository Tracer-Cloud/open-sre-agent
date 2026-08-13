"""Yandex Monitoring metric tools.

The tools read credentials from the shared ``yandex_cloud`` source like every
sibling in the family, and the client hard-codes its own endpoints, so nothing
here needs the generic operation tool or its endpoint index.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from integrations.yc_monitoring.client import resolve_window, summarize_series
from integrations.yc_monitoring.tools import query_yc_metrics

FOLDER = "b1gexamplefolder"
_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the endpoint registry off the network; the snapshot resolves the host."""
    monkeypatch.setattr("integrations.yandex_cloud.endpoints._fetch_endpoints", dict)

    from integrations.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


class TestRegistration:
    def test_both_tools_register_under_the_family_source(self) -> None:
        from tools.registry import clear_tool_registry_cache, get_registered_tools

        clear_tool_registry_cache()
        names = {
            t.name
            for t in get_registered_tools("investigation")
            if str(t.source) == "yc_monitoring"
        }

        assert names == {"query_yc_metrics", "list_yc_metrics"}


class TestWindow:
    def test_a_blank_window_defaults_to_the_recent_past(self) -> None:
        start, end = resolve_window("", "", 30)

        assert start < end

    def test_an_explicit_window_is_kept(self) -> None:
        start, end = resolve_window("2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z", 30)

        assert start == "2026-07-01T00:00:00Z"
        assert end == "2026-07-01T01:00:00Z"


class TestSummary:
    def test_a_series_is_reduced_to_its_shape(self) -> None:
        summary = summarize_series({"name": "cpu", "timeseries": {"doubleValues": [5.0, 95.0]}})

        assert summary["points"] == 2
        assert summary["min"] == 5.0
        assert summary["max"] == 95.0

    def test_an_empty_series_has_no_stats(self) -> None:
        summary = summarize_series({"name": "cpu", "timeseries": {}})

        assert summary["points"] == 0
        assert "max" not in summary


class TestMetricReads:
    def test_the_folder_travels_in_the_query_string_not_the_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitoring ignores folderId in the POST body, which reads as no data."""
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = kwargs["params"]
            captured["body"] = kwargs["json"]
            return httpx.Response(200, json={"metrics": []})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        query_yc_metrics(query='cpu_usage{service="compute"}', **_CREDENTIALS)

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/monitoring/v2/data/read")
        assert captured["params"]["folderId"] == FOLDER
        assert "folderId" not in captured["body"]

    def test_series_come_back_summarized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(
                200,
                json={
                    "metrics": [
                        {
                            "name": "cpu_usage",
                            "labels": {"host": "vm-1"},
                            "timeseries": {"doubleValues": [5.0, 95.0]},
                        }
                    ]
                },
            ),
        )

        result = query_yc_metrics(query="cpu_usage", **_CREDENTIALS)

        assert result["series_count"] == 1
        assert result["series"][0]["max"] == 95.0

    def test_an_unknown_aggregation_is_rejected_without_a_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"n": 0}

        def _request(*_a: Any, **_k: Any) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = query_yc_metrics(query="cpu_usage", aggregation="MEDIAN", **_CREDENTIALS)

        assert "error" in result
        assert called["n"] == 0
