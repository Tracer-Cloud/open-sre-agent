"""Tests for the Coralogix service client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.models import CoralogixIntegrationConfig
from app.integrations.probes import ProbeResult
from app.services.coralogix.client import (
    CoralogixClient,
    _ensure_limit_clause,
    _escape_query_value,
    build_coralogix_logs_query,
)

# -------------------------
# Fixtures
# -------------------------


@pytest.fixture
def config() -> CoralogixIntegrationConfig:
    return CoralogixIntegrationConfig(
        api_key="cx_test_key",
        base_url="https://api.coralogix.com",
        application_name="prod-app",
        subsystem_name="prod-subsystem",
    )


@pytest.fixture
def client(config: CoralogixIntegrationConfig) -> CoralogixClient:
    return CoralogixClient(config)


# -------------------------
# _escape_query_value
# -------------------------


def test_escape_query_value_plain() -> None:
    assert _escape_query_value("hello world") == "hello world"


def test_escape_query_value_backslash() -> None:
    assert _escape_query_value("a\\b") == "a\\\\b"


def test_escape_query_value_single_quote() -> None:
    assert _escape_query_value("it's") == "it\\'s"


def test_escape_query_value_both() -> None:
    assert _escape_query_value("path\\'file") == "path\\\\\\'file"


# -------------------------
# _ensure_limit_clause
# -------------------------


def test_ensure_limit_clause_no_limit() -> None:
    assert _ensure_limit_clause("source logs", 50) == "source logs | limit 50"


def test_ensure_limit_clause_existing_limit() -> None:
    assert _ensure_limit_clause("source logs | limit 10", 50) == "source logs | limit 10"


def test_ensure_limit_clause_limit_in_middle() -> None:
    assert (
        _ensure_limit_clause("source logs | limit 10 | sort @timestamp", 50)
        == "source logs | limit 10 | sort @timestamp"
    )


def test_ensure_limit_clause_strips_whitespace() -> None:
    assert _ensure_limit_clause("  source logs  ", 50) == "source logs | limit 50"


def test_ensure_limit_clause_min_limit_is_1() -> None:
    assert _ensure_limit_clause("source logs", 0) == "source logs | limit 1"
    assert _ensure_limit_clause("source logs", -5) == "source logs | limit 1"


# -------------------------
# build_coralogix_logs_query
# -------------------------


def test_build_coralogix_logs_query_raw_query() -> None:
    query = build_coralogix_logs_query(raw_query="source logs | limit 5", limit=10)
    # raw_query bypasses all field-based filters; _ensure_limit_clause still
    # applies the caller's limit only when no limit is already present
    assert query == "source logs | limit 5"


def test_build_coralogix_logs_query_application_only() -> None:
    query = build_coralogix_logs_query(application_name="my-app")
    assert "source logs" in query
    assert "filter $l.applicationname == 'my-app'" in query


def test_build_coralogix_logs_query_subsystem_only() -> None:
    query = build_coralogix_logs_query(subsystem_name="my-sub")
    assert "filter $l.subsystemname == 'my-sub'" in query


def test_build_coralogix_logs_query_trace_id_only() -> None:
    query = build_coralogix_logs_query(trace_id="abc-123")
    assert "filter $d.trace_id == 'abc-123'" in query


def test_build_coralogix_logs_query_text_query_only() -> None:
    query = build_coralogix_logs_query(text_query="error occurred")
    assert "filter $d.message.contains('error occurred')" in query


def test_build_coralogix_logs_query_all_filters() -> None:
    query = build_coralogix_logs_query(
        application_name="app",
        subsystem_name="sub",
        trace_id="t-1",
        text_query="msg",
        limit=25,
    )
    assert "filter $l.applicationname == 'app'" in query
    assert "filter $l.subsystemname == 'sub'" in query
    assert "filter $d.trace_id == 't-1'" in query
    assert "filter $d.message.contains('msg')" in query
    assert "limit 25" in query


def test_build_coralogix_logs_query_escape_single_quote_in_filter() -> None:
    query = build_coralogix_logs_query(application_name="app's env")
    assert "app\\'s env" in query


def test_build_coralogix_logs_query_default_limit() -> None:
    query = build_coralogix_logs_query(application_name="app")
    assert "limit 50" in query


# -------------------------
# is_configured
# -------------------------


def test_is_configured_both_present(client: CoralogixClient) -> None:
    assert client.is_configured is True


def test_is_configured_missing_api_key(config: CoralogixIntegrationConfig) -> None:
    config.api_key = ""
    client = CoralogixClient(config)
    assert client.is_configured is False


def test_is_configured_missing_base_url(config: CoralogixIntegrationConfig) -> None:
    config.base_url = ""
    client = CoralogixClient(config)
    assert client.is_configured is False


def test_is_configured_both_missing(config: CoralogixIntegrationConfig) -> None:
    config.api_key = ""
    config.base_url = ""
    client = CoralogixClient(config)
    assert client.is_configured is False


# -------------------------
# query_url
# -------------------------


def test_query_url(client: CoralogixClient) -> None:
    assert client.query_url == "https://api.coralogix.com/api/v1/dataprime/query"


# -------------------------
# _request_headers
# -------------------------


def test_request_headers(client: CoralogixClient, config: CoralogixIntegrationConfig) -> None:
    headers = client._request_headers()
    assert headers["Authorization"] == f"Bearer {config.api_key}"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"


# -------------------------
# query_logs — success
# -------------------------


def test_query_logs_success(client: CoralogixClient) -> None:
    ndjson_response = (
        '{"queryId":{"queryId":"q-abc"}}\n'
        '{"result":{"results":[{"metadata":[],"labels":[{"key":"applicationname","value":"app"},'
        '{"key":"subsystemname","value":"sub"}],"userData":{"log_obj":{"message":"log msg"}}}]}}\n'
    )

    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs | limit 10", time_range_minutes=30, limit=10)

    assert result["success"] is True
    assert result["total"] == 1
    assert result["query_ids"] == ["q-abc"]
    assert len(result["logs"]) == 1
    log = result["logs"][0]
    assert log["message"] == "log msg"
    assert log["application_name"] == "app"


def test_query_logs_empty_response(client: CoralogixClient) -> None:
    mock_response = MagicMock()
    mock_response.text = ""
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is True
    assert result["total"] == 0
    assert result["logs"] == []


def test_query_logs_warning_in_response(client: CoralogixClient) -> None:
    ndjson_response = '{"warning":"query took longer than expected"}\n{"result":{"results":[]}}\n'

    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is True
    assert "query took longer than expected" in result["warnings"]


def test_query_logs_background_results(client: CoralogixClient) -> None:
    # Coralogix returns results via two NDJSON paths:
    #   - 'result' -> 'results' (direct query results)
    #   - 'response' -> 'results' (background query results)
    # This tests the 'response' path specifically
    ndjson_response = (
        '{"response":{"results":{"results":['
        '{"metadata":[],"labels":[{"key":"applicationname","value":"bg-app"}],'
        '"userData":{"log_obj":{"message":"background log"}}}'
        "]}}}\n"
    )

    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs", limit=10)

    assert result["success"] is True
    assert result["total"] == 1
    log = result["logs"][0]
    assert log["application_name"] == "bg-app"
    assert log["message"] == "background log"


# -------------------------
# query_logs — error paths
# -------------------------


def test_query_logs_http_error(client: CoralogixClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    error = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=MagicMock(),
        response=mock_response,
    )
    mock_response.raise_for_status.side_effect = error

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is False
    assert "HTTP 401" in result["error"]


def test_query_logs_generic_exception(client: CoralogixClient) -> None:
    with patch("app.services.coralogix.client.httpx.post", side_effect=Exception("DNS failure")):
        result = client.query_logs("source logs")

    assert result["success"] is False
    assert "DNS failure" in result["error"]


# -------------------------
# validate_access
# -------------------------


def test_validate_access_success(client: CoralogixClient) -> None:
    ndjson_response = '{"result":{"results":[{"metadata":{}}]}}\n'

    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.validate_access()

    assert result["success"] is True
    assert result["total"] == 1


def test_validate_access_failure_propagates(client: CoralogixClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server error"

    error = httpx.HTTPStatusError(
        "500 Server error",
        request=MagicMock(),
        response=mock_response,
    )
    mock_response.raise_for_status.side_effect = error

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.validate_access()

    assert result["success"] is False
    assert "HTTP 500" in result["error"]


# -------------------------
# probe_access
# -------------------------


def test_probe_access_passed(client: CoralogixClient) -> None:
    # Uses default config (no application_name / subsystem_name set)
    # so the passed detail has no scope suffixes
    ndjson_response = '{"result":{"results":[{"metadata":{}}]}}\n'

    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.probe_access()

    assert result.status == "passed"
    assert "Connected to" in result.detail
    assert "row(s)" in result.detail


def test_probe_access_missing_config(config: CoralogixIntegrationConfig) -> None:
    config.api_key = ""
    client = CoralogixClient(config)
    result = client.probe_access()

    assert result.status == "missing"
    assert "Missing Coralogix API key or API URL" in result.detail


def test_probe_access_failed(client: CoralogixClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    error = httpx.HTTPStatusError(
        "403 Forbidden",
        request=MagicMock(),
        response=mock_response,
    )
    mock_response.raise_for_status.side_effect = error

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.probe_access()

    assert result.status == "failed"
    assert "DataPrime check failed" in result.detail


def test_probe_access_with_scope(config: CoralogixIntegrationConfig) -> None:
    # config has application_name and subsystem_name set in fixture
    # verify scope details are surfaced in the passed detail (distinct from
    # test_probe_access_passed which uses the default config with no scope)
    client = CoralogixClient(config)

    ndjson_response = '{"result":{"results":[{"metadata":{}}]}}\n'
    mock_response = MagicMock()
    mock_response.text = ndjson_response
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.coralogix.client.httpx.post", return_value=mock_response):
        result = client.probe_access()

    assert result.status == "passed"
    assert "application prod-app" in result.detail
    assert "subsystem prod-subsystem" in result.detail
    assert "Connected to" in result.detail
    assert "row(s)" in result.detail
