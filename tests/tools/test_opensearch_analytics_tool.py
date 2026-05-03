"""Dedicated unit tests for OpenSearchAnalyticsTool.

Covers contract metadata, source-availability gating, parameter extraction,
client config normalization, bounded result limits, log filtering/normalization
on the tool side (non-dict items dropped), and propagation of client errors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.services.elasticsearch import ElasticsearchConfig
from app.tools.OpenSearchAnalyticsTool import query_opensearch_analytics
from tests.tools.conftest import BaseToolContract

# ---------------------------------------------------------------------------
# Test helpers — keep ElasticsearchClient stubbing consistent
# ---------------------------------------------------------------------------


def _install_es_stubs(
    monkeypatch: pytest.MonkeyPatch,
    search_impl: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Patch ``ElasticsearchClient.__init__`` AND ``search_logs`` together.

    Returns a dict that captures the ``ElasticsearchConfig`` the tool builds,
    accessible as ``captured["config"]`` after the tool is invoked. Patching
    both methods keeps every test isolated from the real client even if
    ``__init__`` ever stops being lazy (e.g. starts validating URL reachability).
    """
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, config: ElasticsearchConfig) -> None:
        captured["config"] = config
        self.config = config

    monkeypatch.setattr(
        "app.tools.OpenSearchAnalyticsTool.ElasticsearchClient.__init__",
        _fake_init,
    )
    monkeypatch.setattr(
        "app.tools.OpenSearchAnalyticsTool.ElasticsearchClient.search_logs",
        search_impl,
    )
    return captured


def _ok_search(_self: Any, **_kwargs: Any) -> dict[str, Any]:
    """Default ``search_logs`` stub — succeeds with an empty result set."""
    return {"success": True, "logs": []}


# ---------------------------------------------------------------------------
# Contract — metadata, is_available, extract_params surface
# ---------------------------------------------------------------------------


class TestOpenSearchAnalyticsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_opensearch_analytics.__opensre_registered_tool__


def _rt() -> Any:
    return query_opensearch_analytics.__opensre_registered_tool__


def test_is_available_true_when_url_and_verified() -> None:
    sources = {
        "opensearch": {
            "connection_verified": True,
            "url": "https://os.example.invalid",
        }
    }
    assert _rt().is_available(sources) is True


def test_is_available_false_when_connection_not_verified() -> None:
    sources = {
        "opensearch": {
            "connection_verified": False,
            "url": "https://os.example.invalid",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_url_missing() -> None:
    sources = {"opensearch": {"connection_verified": True}}
    assert _rt().is_available(sources) is False


def test_is_available_false_when_no_opensearch_source() -> None:
    assert _rt().is_available({}) is False


def test_extract_params_strips_and_uses_defaults() -> None:
    sources = {
        "opensearch": {
            "url": "  https://os.example.invalid  ",
            "api_key": "  os-key  ",
            "index_pattern": "  logs-*  ",
            "default_query": "  service:foo  ",
            "integration_id": " os-1 ",
        }
    }
    params = _rt().extract_params(sources)
    assert params["url"] == "https://os.example.invalid"
    assert params["api_key"] == "os-key"
    assert params["index_pattern"] == "logs-*"
    assert params["query"] == "service:foo"
    assert params["time_range_minutes"] == 60  # default
    assert params["limit"] == 50
    assert params["max_results"] == 100  # _DEFAULT_MAX_RESULTS
    assert params["integration_id"] == "os-1"


def test_extract_params_falls_back_to_star_for_blank_index_and_query() -> None:
    sources = {
        "opensearch": {
            "url": "https://os.example.invalid",
            "index_pattern": "  ",
            "default_query": "",
        }
    }
    params = _rt().extract_params(sources)
    assert params["index_pattern"] == "*"
    assert params["query"] == "*"


def test_extract_params_uses_default_max_results_when_zero() -> None:
    sources = {"opensearch": {"url": "https://os.example.invalid", "max_results": 0}}
    assert _rt().extract_params(sources)["max_results"] == 100


# ---------------------------------------------------------------------------
# Validation guards — missing config
# ---------------------------------------------------------------------------


def test_returns_missing_url_when_url_blank() -> None:
    result = query_opensearch_analytics(url="   ")
    assert result["available"] is False
    assert result["error"] == "Missing OpenSearch URL."
    assert result["logs"] == []


# ---------------------------------------------------------------------------
# Client config normalization — what gets passed to ElasticsearchClient
# ---------------------------------------------------------------------------


def test_client_config_normalizes_url_api_key_and_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_es_stubs(monkeypatch, _ok_search)

    query_opensearch_analytics(
        url="  https://os.example.invalid/  ",  # trailing slash + spaces stripped
        api_key="  os-key  ",
        index_pattern="logs-prod-*",
    )
    cfg = captured["config"]
    assert isinstance(cfg, ElasticsearchConfig)
    assert cfg.url == "https://os.example.invalid"
    assert cfg.api_key == "os-key"
    assert cfg.index_pattern == "logs-prod-*"


def test_client_config_treats_blank_api_key_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_es_stubs(monkeypatch, _ok_search)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        api_key="   ",
        index_pattern="*",
    )
    assert captured["config"].api_key is None


def test_blank_index_pattern_becomes_star(monkeypatch: pytest.MonkeyPatch) -> None:
    search_kwargs: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        search_kwargs.update(kwargs)
        return {"success": True, "logs": []}

    captured = _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid", index_pattern="")
    assert captured["config"].index_pattern == "*"
    assert search_kwargs["index_pattern"] == "*"
    assert result["index_pattern"] == "*"


# ---------------------------------------------------------------------------
# Search call shape — query, time range, bounded limit
# ---------------------------------------------------------------------------


def test_search_query_defaults_to_star_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        query="",
    )
    assert captured["query"] == "*"
    assert result["query"] == "*"


def test_time_range_minutes_floor_one(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        time_range_minutes=0,
    )
    # max(1, time_range_minutes) — never zero or negative
    assert captured["time_range_minutes"] == 1


def test_bounded_limit_caps_caller_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured["limit"] = kwargs["limit"]
        # Server returns more than the cap — tool must trim to effective_limit
        return {
            "success": True,
            "logs": [{"message": f"log-{idx}"} for idx in range(20)],
        }

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        limit=999,
        max_results=5,
    )
    assert captured["limit"] == 5
    assert len(result["logs"]) == 5
    assert result["total_returned"] == 5


def test_bounded_limit_capped_by_hard_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caller may not request more than _MAX_HARD_LIMIT (200), even via max_results."""
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured["limit"] = kwargs["limit"]
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        limit=10_000,
        max_results=10_000,
    )
    assert captured["limit"] == 200


# ---------------------------------------------------------------------------
# Response normalization — non-dict log entries are dropped
# ---------------------------------------------------------------------------


def test_filters_non_dict_log_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "logs": [
                {"message": "ok"},
                "garbage-string",
                {"message": "ok2"},
                None,
                42,
            ],
        }

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        max_results=10,
    )
    assert result["available"] is True
    assert result["logs"] == [{"message": "ok"}, {"message": "ok2"}]
    assert result["total_returned"] == 2


def test_handles_missing_logs_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": True}  # no 'logs' key at all

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is True
    assert result["logs"] == []


def test_handles_non_list_logs_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": True, "logs": "should-be-a-list-but-isnt"}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is True
    assert result["logs"] == []


# ---------------------------------------------------------------------------
# Client error propagation
# ---------------------------------------------------------------------------


def test_propagates_client_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "auth failed"}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is False
    assert "auth failed" in result["error"]
    assert result["logs"] == []


def test_propagates_unknown_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False}  # no 'error' field

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is False
    # Tool falls back to a generic message rather than raising
    assert "Unknown OpenSearch error" in result["error"]
