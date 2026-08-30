"""Tests for AzureMonitorLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from integrations.azure.tools.azure_monitor_logs_tool import (
    _bounded_limit,
    _ensure_take_clause,
    query_azure_monitor_logs,
)
from integrations.azure.tools.azure_monitor_logs_tool._evidence import (
    map_query_azure_monitor_logs as _map_query_azure_monitor_logs,
)
from tests.tools.conftest import BaseToolContract


def _registered_tool() -> Any:
    return cast(Any, query_azure_monitor_logs).__opensre_registered_tool__


class TestAzureMonitorLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool()


@pytest.mark.parametrize(
    "sources,expected",
    [
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "workspace-123",
                    "access_token": "token-abc",
                }
            },
            True,
        ),
        (
            {
                "azure": {
                    "connection_verified": False,
                    "workspace_id": "workspace-123",
                    "access_token": "token-abc",
                }
            },
            False,
        ),
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "",
                    "access_token": "token-abc",
                }
            },
            False,
        ),
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "workspace-123",
                    "access_token": "",
                }
            },
            False,
        ),
        ({}, False),
    ],
)
def test_is_available_requires_verified_workspace_and_token(sources: dict, expected: bool) -> None:
    rt = _registered_tool()
    assert rt.is_available(sources) is expected


def test_extract_params_maps_fields_and_defaults() -> None:
    rt = _registered_tool()
    params = rt.extract_params(
        {
            "azure": {
                "workspace_id": " workspace-123 ",
                "access_token": " token-abc ",
                "endpoint": " https://api.loganalytics.io ",
            }
        }
    )

    assert params["workspace_id"] == "workspace-123"
    assert params["access_token"] == "token-abc"
    assert params["endpoint"] == "https://api.loganalytics.io"
    assert params["time_range_minutes"] == 60
    assert params["limit"] == 50


def test_bounded_limit_caps_requested_limit() -> None:
    assert _bounded_limit(300, 100) == 100


def test_bounded_limit_enforces_hard_ceiling() -> None:
    # max_results above _MAX_HARD_LIMIT (200) must still be capped at 200
    assert _bounded_limit(500, 300) == 200


def test_bounded_limit_enforces_minimum_of_one() -> None:
    assert _bounded_limit(0, 100) == 1
    assert _bounded_limit(-10, 100) == 1


@pytest.mark.parametrize(
    "query,limit,expected",
    [
        ("", 10, "AppTraces | order by TimeGenerated desc | take 10"),
        (
            "AppTraces | order by TimeGenerated desc",
            5,
            "AppTraces | order by TimeGenerated desc | take 5",
        ),
        ("AppTraces | take 100", 5, "AppTraces | take 100"),
        ("AppTraces | limit 100", 5, "AppTraces | limit 100"),
    ],
)
def test_ensure_take_clause_branches(query: str, limit: int, expected: str) -> None:
    assert _ensure_take_clause(query, limit) == expected


def test_ensure_take_clause_appends_real_bound_despite_quoted_take_text() -> None:
    """Regression: a naive substring check for "take"/"limit" would match
    inside a quoted filter value and wrongly skip appending the real safety
    cap, letting the query run unbounded against the customer's workspace."""
    query = 'AppTraces | where Message contains "| take 5 now"'
    assert _ensure_take_clause(query, 5) == query + " | take 5"


def test_run_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked_response = MagicMock()
    mocked_response.raise_for_status.return_value = None
    mocked_response.json.return_value = {
        "tables": [
            {
                "columns": [
                    {"name": "TimeGenerated"},
                    {"name": "Message"},
                ],
                "rows": [
                    ["2026-04-27T10:00:00Z", "error: failed to connect"],
                    ["2026-04-27T10:01:00Z", "info: retry succeeded"],
                ],
            }
        ]
    }

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return mocked_response

    monkeypatch.setattr("integrations.azure.tools.azure_monitor_logs_tool.httpx.post", fake_post)

    result = query_azure_monitor_logs(
        workspace_id="workspace-123",
        access_token="token-abc",
        query="AppTraces | order by TimeGenerated desc",
        limit=2,
    )

    assert result["available"] is True
    assert result["source"] == "azure"
    assert result["total_returned"] == 2
    assert result["rows"][0]["Message"] == "error: failed to connect"
    # Assert the outgoing request was constructed correctly
    assert "workspace-123" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert "query" in captured["json"]


def test_run_http_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked_response = MagicMock()
    mocked_response.raise_for_status.side_effect = Exception("401 Client Error: Unauthorized")
    mocked_response.json.return_value = {}

    monkeypatch.setattr(
        "integrations.azure.tools.azure_monitor_logs_tool.httpx.post",
        lambda *_args, **_kwargs: mocked_response,
    )

    result = query_azure_monitor_logs(
        workspace_id="workspace-123",
        access_token="token-abc",
        query="AppTraces",
    )

    assert "error" in result
    assert "401" in result["error"]
    assert result["source"] == "azure"
    assert result["available"] is False
    assert result["rows"] == []


def test_run_unavailable_without_credentials() -> None:
    result = query_azure_monitor_logs(workspace_id="", access_token="", query="AppTraces")

    assert result["available"] is False
    assert "missing azure credentials" in result["error"].lower()


class TestMapQueryAzureMonitorLogs:
    def test_records_entry_with_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 2,
                "effective_limit": 50,
                "query": "AppTraces | order by TimeGenerated desc | take 50",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_azure_monitor_logs"
        assert (
            entries[0]["summary"]
            == "2 row(s) for query 'AppTraces | order by TimeGenerated desc | take 50'"
        )

    def test_qualifies_count_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 50,
                "effective_limit": 50,
                "query": "AppTraces | take 50",
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("50+ row(s)")

    def test_qualifies_count_when_caller_query_has_a_smaller_take_clause(self) -> None:
        """Regression: _ensure_take_clause leaves a caller-supplied query
        untouched when it already has a `take` stage, so effective_limit is
        never actually applied server-side -- the caller's own smaller
        `take N` is the real ceiling and must be detected."""
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 5,
                "effective_limit": 50,
                "query": "AppTraces | where Level == 'Error' | take 5",
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("5+ row(s)")

    def test_qualifies_count_when_caller_query_has_a_smaller_limit_clause(self) -> None:
        """Regression: KQL defines `limit` as a synonym for `take`, so a
        caller-supplied `| limit N` clause is just as real a ceiling as
        `| take N` and must be detected the same way."""
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 5,
                "effective_limit": 50,
                "query": "AppTraces | where Level == 'Error' | limit 5",
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("5+ row(s)")

    def test_does_not_qualify_when_caller_take_clause_was_not_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 3,
                "effective_limit": 50,
                "query": "AppTraces | take 5",
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("3 row(s)")

    def test_ignores_take_text_that_is_not_a_real_pipe_stage(self) -> None:
        """Regression: 'take N' inside a quoted string literal or a comment
        is not an actual KQL take operator (which requires a preceding `|`)
        -- matching it as one would falsely mark a complete result as
        truncated."""
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 3,
                "effective_limit": 50,
                "query": 'AppTraces | where Message contains "take 5 minutes"',
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("3 row(s)")

    def test_ignores_pipe_and_take_text_embedded_in_a_quoted_string(self) -> None:
        """Regression: a literal '|' immediately before 'take N' inside a
        quoted string literal (e.g. a filter value) is not a real KQL
        pipe-stage boundary -- the pipe-anchored regex alone still matches
        it, so the query text must be masked before the regex runs."""
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 3,
                "effective_limit": 50,
                "query": 'AppTraces | where Message contains "| take 5 now"',
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("3 row(s)")

    def test_strips_carriage_returns_from_query(self) -> None:
        """Regression: a query with bare \\r or \\r\\n line endings must not
        leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence,
            {
                "available": True,
                "total_returned": 1,
                "effective_limit": 50,
                "query": "AppTraces\r\n| take 1\r",
            },
            {},
        )

        assert "\r" not in evidence["catalog_entries"][0]["summary"]

    def test_records_nothing_when_no_rows(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(
            evidence, {"available": True, "total_returned": 0, "rows": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_azure_monitor_logs(evidence, {"available": False, "error": "401"}, {})

        assert "catalog_entries" not in evidence
