"""Tests for PrefectClient request bodies and helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from app.services.prefect.client import PrefectClient, PrefectConfig


def test_get_flow_runs_sends_state_type_and_name() -> None:
    cfg = PrefectConfig(api_url="http://localhost:4200/api")
    client = PrefectClient(cfg)
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_http.post.return_value = mock_response
    client._client = mock_http

    client.get_flow_runs(
        limit=5,
        states=["failed", "crashed"],
        state_names=["Failed", "Crashed"],
    )

    mock_http.post.assert_called_once()
    _path, kwargs = mock_http.post.call_args
    assert _path[0] == "/flow_runs/filter"
    body = kwargs["json"]
    assert body["limit"] == 5
    assert body["sort"] == "START_TIME_DESC"
    assert body["flow_runs"]["state"]["type"]["any_"] == ["FAILED", "CRASHED"]
    assert body["flow_runs"]["state"]["name"]["any_"] == ["Failed", "Crashed"]


def test_get_flow_runs_omits_filter_when_no_state_criteria() -> None:
    cfg = PrefectConfig(api_url="http://localhost:4200/api")
    client = PrefectClient(cfg)
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_http.post.return_value = mock_response
    client._client = mock_http

    client.get_flow_runs(limit=10, states=None, state_names=None)

    body = mock_http.post.call_args.kwargs["json"]
    assert "flow_runs" not in body


def test_get_task_runs_requires_some_scope() -> None:
    cfg = PrefectConfig(api_url="http://localhost:4200/api")
    client = PrefectClient(cfg)

    out = client.get_task_runs(flow_run_id=None, states=None, state_names=None, limit=10)
    assert out["success"] is False
    assert out["task_runs"] == []
    assert out["total"] == 0


def test_get_task_runs_http_error_includes_empty_task_runs() -> None:
    cfg = PrefectConfig(api_url="http://localhost:4200/api")
    client = PrefectClient(cfg)
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad",
        request=MagicMock(),
        response=MagicMock(status_code=500, text="x"),
    )
    mock_http.post.return_value = mock_resp
    client._client = mock_http

    out = client.get_task_runs(flow_run_id="run-1", states=["FAILED"], state_names=None)
    assert out["success"] is False
    assert out["task_runs"] == []
    assert out["total"] == 0


def test_get_task_runs_posts_expected_filter() -> None:
    cfg = PrefectConfig(api_url="http://localhost:4200/api")
    client = PrefectClient(cfg)
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_http.post.return_value = mock_response
    client._client = mock_http

    client.get_task_runs(
        flow_run_id="abc-123",
        limit=3,
        states=["FAILED"],
        state_names=["Failed"],
    )

    mock_http.post.assert_called_once()
    path, kwargs = mock_http.post.call_args
    assert path[0] == "/task_runs/filter"
    body = kwargs["json"]
    assert body["limit"] == 3
    assert body["sort"] == "EXPECTED_START_TIME_DESC"
    assert body["task_runs"]["flow_run_id"]["any_"] == ["abc-123"]
    assert body["task_runs"]["state"]["type"]["any_"] == ["FAILED"]
    assert body["task_runs"]["state"]["name"]["any_"] == ["Failed"]
