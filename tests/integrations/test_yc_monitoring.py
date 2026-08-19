"""Yandex Monitoring metric tools.

The tools read credentials from the shared ``yandex_cloud`` source like every
sibling in the family, and the client hard-codes its own endpoints, so nothing
here needs the generic operation tool or its endpoint index.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.monitoring_client import resolve_window, summarize_series
from integrations.yandex_cloud.tools import list_yc_metrics, query_yc_metrics

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
        by_name = {
            t.name: str(t.source)
            for t in get_registered_tools("investigation")
            if t.name in {"query_yc_metrics", "list_yc_metrics"}
        }

        assert by_name == {
            "query_yc_metrics": "yandex_cloud",
            "list_yc_metrics": "yandex_cloud",
        }


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
            return httpx.Response(HTTPStatus.OK, json={"metrics": []})

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
            return httpx.Response(HTTPStatus.OK, json={})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = query_yc_metrics(query="cpu_usage", aggregation="MEDIAN", **_CREDENTIALS)

        assert "error" in result
        assert called["n"] == 0


def _discovery_responder(names: list[str], keys: list[str]) -> Any:
    """Return a request stand-in answering the two discovery endpoints."""

    def _request(_method: str, url: str, **_kwargs: Any) -> httpx.Response:
        if "/names" in url:
            return httpx.Response(HTTPStatus.OK, json={"names": names})
        return httpx.Response(HTTPStatus.OK, json={"keys": keys})

    return _request


class TestMetricDiscovery:
    def test_names_and_labels_come_back_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One call has to answer both, or the caller cannot build a query at all."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _discovery_responder(["cpu_usage", "memory_usage"], ["host", "service"]),
        )

        result = list_yc_metrics(**_CREDENTIALS)

        assert result["names"] == ["cpu_usage", "memory_usage"]
        assert result["labels"] == ["host", "service"]
        assert result["name_count"] == 2

    def test_the_name_filter_narrows_locally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Yandex accepts nameFilter and ignores it, so every filter would return the lot."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _discovery_responder(["cpu_usage", "memory_usage"], ["host"]),
        )

        result = list_yc_metrics(name_filter="MEMORY", **_CREDENTIALS)

        assert result["names"] == ["memory_usage"]

    def test_an_empty_default_scope_points_at_custom_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing found usually means the wrong scope, not that the folder is empty."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _discovery_responder([], []),
        )

        result = list_yc_metrics(**_CREDENTIALS)

        assert 'service="custom"' in result["note"]

    def test_an_explicit_scope_is_reported_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _discovery_responder(["pushed_metric"], []),
        )

        result = list_yc_metrics(selectors='service="custom"', **_CREDENTIALS)

        assert result["scope"] == 'service="custom"'
        assert "note" not in result


class TestTheQueryLanguageIsDocumented:
    """The schema text is the only thing stopping the model writing PromQL.

    It is prose inside a long constant, which is exactly what a prompt-budget
    trim deletes without anyone noticing the tool got worse.
    """

    def test_it_says_the_language_is_not_promql(self) -> None:
        from tools.registry import get_registered_tool_map

        schema = get_registered_tool_map("investigation")["query_yc_metrics"].input_schema
        description = schema["properties"]["query"]["description"]

        assert "NOT PromQL" in description
        assert "series_sum" in description

    def test_it_calls_out_the_folder_label_trap(self) -> None:
        """`folderId` parses fine and silently matches nothing, which reads as no data."""
        from tools.registry import get_registered_tool_map

        schema = get_registered_tool_map("investigation")["query_yc_metrics"].input_schema
        description = schema["properties"]["query"]["description"]

        assert "folder_id" in description
        assert "folderId" in description
