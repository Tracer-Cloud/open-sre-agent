"""Dedicated unit tests for OpenObserveLogsTool.

Covers contract metadata, source-availability gating (token vs basic auth),
parameter extraction, default query fallback, bounded result limits, record
extraction across the supported response shapes (Elastic-style hits.hits,
top-level list, ``records`` field, ``data`` field), bearer vs basic
``Authorization`` header behavior, and HTTP failure handling.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from app.tools.OpenObserveLogsTool import query_openobserve_logs
from tests.tools.conftest import BaseToolContract, MockHttpxResponse

# ---------------------------------------------------------------------------
# Contract — metadata, is_available, extract_params surface
# ---------------------------------------------------------------------------


class TestOpenObserveLogsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_openobserve_logs.__opensre_registered_tool__


def _rt() -> Any:
    return query_openobserve_logs.__opensre_registered_tool__


def test_is_available_true_with_api_token() -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "base_url": "https://oo.example.invalid",
            "api_token": "oo-token",
        }
    }
    assert _rt().is_available(sources) is True


def test_is_available_true_with_username_password() -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "base_url": "https://oo.example.invalid",
            "username": "u",
            "password": "p",
        }
    }
    assert _rt().is_available(sources) is True


def test_is_available_false_without_credentials() -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "base_url": "https://oo.example.invalid",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_only_username_no_password() -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "base_url": "https://oo.example.invalid",
            "username": "u",
            "password": "",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_connection_unverified() -> None:
    sources = {
        "openobserve": {
            "connection_verified": False,
            "base_url": "https://oo.example.invalid",
            "api_token": "oo-token",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_without_base_url() -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "api_token": "oo-token",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_no_openobserve_source() -> None:
    assert _rt().is_available({}) is False


def test_extract_params_strips_and_uses_defaults() -> None:
    sources = {
        "openobserve": {
            "base_url": "  https://oo.example.invalid  ",
            "stream": " app_logs ",
            "query": "  level = 'error'  ",
            "api_token": "  oo-token  ",
            "username": " ",
            "password": " ",
            "integration_id": " oo-1 ",
        }
    }
    params = _rt().extract_params(sources)
    assert params["base_url"] == "https://oo.example.invalid"
    assert params["org"] == "default"  # default fallback
    assert params["stream"] == "app_logs"
    assert params["query"] == "level = 'error'"
    assert params["api_token"] == "oo-token"
    assert params["username"] == ""
    assert params["password"] == ""
    assert params["time_range_minutes"] == 60  # default
    assert params["limit"] == 50
    assert params["max_results"] == 100  # _DEFAULT_MAX_RESULTS
    assert params["integration_id"] == "oo-1"


def test_extract_params_falls_back_to_default_org_for_blank() -> None:
    sources = {"openobserve": {"base_url": "https://x.invalid", "org": "   "}}
    assert _rt().extract_params(sources)["org"] == "default"


def test_extract_params_uses_default_max_results_when_zero() -> None:
    sources = {"openobserve": {"base_url": "https://x.invalid", "max_results": 0}}
    assert _rt().extract_params(sources)["max_results"] == 100


# ---------------------------------------------------------------------------
# Validation guards — missing config / required fields
# ---------------------------------------------------------------------------


def test_returns_missing_url_when_base_url_blank() -> None:
    result = query_openobserve_logs(base_url="   ", api_token="oo-token")
    assert result["available"] is False
    assert result["error"] == "Missing OpenObserve URL."
    assert result["records"] == []


def test_returns_missing_credentials_when_no_auth_provided() -> None:
    result = query_openobserve_logs(base_url="https://oo.example.invalid")
    assert result["available"] is False
    assert result["error"] == "Missing OpenObserve credentials."
    assert result["records"] == []


def test_returns_missing_credentials_when_only_username_no_password() -> None:
    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        username="u",
        password="",
    )
    assert result["available"] is False
    assert result["error"] == "Missing OpenObserve credentials."


# ---------------------------------------------------------------------------
# Auth header behavior — bearer token vs HTTP basic
# ---------------------------------------------------------------------------


def test_uses_bearer_authorization_when_api_token_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["headers"] = headers
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert captured["headers"]["Authorization"] == "Bearer oo-token"
    assert captured["headers"]["Content-Type"] == "application/json"


def test_uses_basic_authorization_when_only_username_password_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["headers"] = headers
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        username="alice",
        password="secret",
    )
    expected = base64.b64encode(b"alice:secret").decode("ascii")
    assert captured["headers"]["Authorization"] == f"Basic {expected}"


def test_bearer_takes_precedence_over_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["headers"] = headers
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        username="alice",
        password="secret",
    )
    # When both are provided, bearer wins
    assert captured["headers"]["Authorization"] == "Bearer oo-token"


# ---------------------------------------------------------------------------
# Request shaping — endpoint, default query fallback, payload contents
# ---------------------------------------------------------------------------


def test_endpoint_uses_default_org_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["url"] = url
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid/",  # trailing slash should be stripped
        org="",
        api_token="oo-token",
    )
    assert captured["url"] == "https://oo.example.invalid/api/default/_search"


def test_endpoint_includes_provided_org(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["url"] = url
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        org="acme",
        api_token="oo-token",
    )
    assert captured["url"] == "https://oo.example.invalid/api/acme/_search"


def test_default_sql_used_when_query_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["payload"] = json
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        query="",
    )
    sql = captured["payload"]["query"]["sql"]
    assert sql == "SELECT * FROM \"default\" WHERE level = 'error' ORDER BY _timestamp DESC"


def test_provided_query_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["payload"] = json
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        query="SELECT * FROM \"my_stream\" WHERE level = 'fatal'",
    )
    assert (
        captured["payload"]["query"]["sql"] == "SELECT * FROM \"my_stream\" WHERE level = 'fatal'"
    )


def test_payload_includes_size_and_time_window(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["payload"] = json
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        time_range_minutes=15,
        max_results=42,
    )
    payload = captured["payload"]
    assert payload["size"] == 42
    assert "start_time" in payload["query"]
    assert "end_time" in payload["query"]
    assert payload["query"]["end_time"] > payload["query"]["start_time"]


def test_stream_name_only_included_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured.append(json)
        return MockHttpxResponse({"hits": []})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    # Without stream
    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        stream="",
    )
    # With stream
    query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        stream="app_logs",
    )

    assert "stream_name" not in captured[0]
    assert captured[1]["stream_name"] == "app_logs"


def test_bounded_limit_caps_caller_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["size"] = json["size"]
        return MockHttpxResponse({"hits": [{"message": f"m{idx}"} for idx in range(50)]})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
        limit=1000,
        max_results=4,
    )
    assert captured["size"] == 4
    assert result["available"] is True
    assert len(result["records"]) == 4
    assert result["total_returned"] == 4


# ---------------------------------------------------------------------------
# Response normalization across supported shapes
# ---------------------------------------------------------------------------


def test_records_from_top_level_hits_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse({"hits": [{"a": 1}, {"a": 2}]})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["records"] == [{"a": 1}, {"a": 2}]


def test_records_from_elastic_style_nested_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse(
            {
                "hits": {
                    "hits": [
                        {"_source": {"msg": "boom"}},
                        {"_source": {"msg": "kaboom"}},
                    ]
                }
            }
        )

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["records"] == [{"msg": "boom"}, {"msg": "kaboom"}]


def test_records_from_records_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse({"records": [{"x": 1}, "ignore-me", {"x": 2}]})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    # Only dict items are kept — strings are filtered out
    assert result["records"] == [{"x": 1}, {"x": 2}]


def test_records_from_data_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse({"data": [{"y": 9}]})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["records"] == [{"y": 9}]


def test_records_empty_on_unrecognized_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse({"unrelated": True})

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["available"] is True
    assert result["records"] == []


# ---------------------------------------------------------------------------
# HTTP failure handling
# ---------------------------------------------------------------------------


def test_returns_unavailable_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(*_args: Any, **_kwargs: Any) -> MockHttpxResponse:
        return MockHttpxResponse(
            {"err": "boom"},
            raise_for_status_error=RuntimeError("HTTP 502 from OpenObserve"),
        )

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _fake_post)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["available"] is False
    assert "HTTP 502 from OpenObserve" in result["error"]
    assert result["records"] == []


def test_returns_unavailable_on_request_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("app.tools.OpenObserveLogsTool.httpx.post", _raise)

    result = query_openobserve_logs(
        base_url="https://oo.example.invalid",
        api_token="oo-token",
    )
    assert result["available"] is False
    assert "connection refused" in result["error"]
    assert result["records"] == []
