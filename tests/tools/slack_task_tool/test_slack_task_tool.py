"""Tests for Slack to GitHub task management tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from integrations.github.client import GitHubApiError
from tools.slack_task_tool.tool import (
    close_github_task_from_slack,
    create_github_task_from_slack,
    update_github_task_from_slack,
)


class MockSource:
    def __init__(self, url: str) -> None:
        self.url = url


class MockSession:
    def __init__(self, source_url: str | None = None) -> None:
        if source_url:
            self.source = MockSource(url=source_url)
        else:
            self.source = None


class MockContext:
    def __init__(self, source_url: str | None = None) -> None:
        self.session = MockSession(source_url=source_url)


@pytest.fixture
def mock_github_client() -> Any:
    with patch("tools.slack_task_tool.tool.GitHubRestClient") as mock_client_cls:
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        yield mock_instance


from core.tool_framework.registered_tool import REGISTERED_TOOL_ATTR

# ---------------------------------------------------------------------------
# Metadata Tests
# ---------------------------------------------------------------------------


def test_tool_metadata() -> None:
    # Ensure side-effect levels and surfaces are correct for mutating tools
    meta1 = getattr(create_github_task_from_slack, REGISTERED_TOOL_ATTR)
    assert meta1.side_effect_level == "mutating"
    assert "chat" in meta1.surfaces

    meta2 = getattr(update_github_task_from_slack, REGISTERED_TOOL_ATTR)
    assert meta2.side_effect_level == "mutating"
    assert "chat" in meta2.surfaces

    meta3 = getattr(close_github_task_from_slack, REGISTERED_TOOL_ATTR)
    assert meta3.side_effect_level == "mutating"
    assert "chat" in meta3.surfaces


# ---------------------------------------------------------------------------
# Creation Tests
# ---------------------------------------------------------------------------


def test_create_github_task_preserves_slack_url(mock_github_client: MagicMock) -> None:
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "title": "Fix the flurb",
        "state": "open",
    }

    mock_context = MockContext(source_url="https://acme.slack.com/archives/C123/p12345")

    result = create_github_task_from_slack(
        owner="owner",
        repo="repo",
        title="Fix the flurb",
        body="Flurb is broken.",
        labels=["bug"],
        assignees=["johndoe"],
        milestone=1,
        context=mock_context,
    )

    assert result["ok"] is True
    assert result["issue_number"] == 123
    assert result["issue_url"] == "https://github.com/owner/repo/issues/123"

    mock_github_client.request.assert_called_once()
    args, kwargs = mock_github_client.request.call_args
    assert args[0] == "POST"
    assert args[1] == "repos/owner/repo/issues"

    payload = kwargs["body"]
    assert payload["title"] == "Fix the flurb"
    assert "Requested from Slack: https://acme.slack.com/archives/C123/p12345" in payload["body"]
    assert payload["labels"] == ["bug"]
    assert payload["assignees"] == ["johndoe"]
    assert payload["milestone"] == 1


@patch("integrations.github.projects_v2.sync_project_fields")
def test_create_github_task_with_projects_v2(
    mock_sync: MagicMock, mock_github_client: MagicMock
) -> None:
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "title": "Fix the flurb",
        "state": "open",
        "node_id": "I_kwHOA",
    }

    result = create_github_task_from_slack(
        owner="owner",
        repo="repo",
        title="Fix the flurb",
        project_number=42,
        project_fields={"Status": "Todo"},
    )

    assert result["ok"] is True

    mock_sync.assert_called_once()
    args, kwargs = mock_sync.call_args
    assert args[1] == "owner"
    assert args[2] == 42
    assert args[3] == "I_kwHOA"
    assert args[4] == {"Status": "Todo"}


# ---------------------------------------------------------------------------
# Update Tests
# ---------------------------------------------------------------------------


def test_update_github_task_syncs_fields(mock_github_client: MagicMock) -> None:
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "state": "open",
        "labels": [{"name": "enhancement"}],
        "assignees": [{"login": "janedoe"}],
    }

    result = update_github_task_from_slack(
        owner="owner",
        repo="repo",
        issue_number=123,
        state="open",
        labels=["enhancement"],
        assignees=["janedoe"],
    )

    assert result["ok"] is True
    assert result["labels"] == ["enhancement"]
    assert result["assignees"] == ["janedoe"]
    assert result["state"] == "open"

    mock_github_client.request.assert_called_once()
    args, kwargs = mock_github_client.request.call_args
    assert args[0] == "PATCH"
    assert args[1] == "repos/owner/repo/issues/123"
    assert kwargs["body"]["state"] == "open"
    assert kwargs["body"]["labels"] == ["enhancement"]
    assert kwargs["body"]["assignees"] == ["janedoe"]


def test_update_github_task_clears_fields(mock_github_client: MagicMock) -> None:
    # Explicitly test clearing labels/assignees by passing empty arrays
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "state": "open",
        "labels": [],
        "assignees": [],
    }

    result = update_github_task_from_slack(
        owner="owner",
        repo="repo",
        issue_number=123,
        labels=[],
        assignees=[],
    )

    assert result["ok"] is True
    assert result["labels"] == []
    assert result["assignees"] == []

    payload = mock_github_client.request.call_args[1]["body"]
    assert payload["labels"] == []
    assert payload["assignees"] == []


@patch("integrations.github.projects_v2.sync_project_fields")
def test_update_github_task_with_projects_v2_only(
    mock_sync: MagicMock, mock_github_client: MagicMock
) -> None:
    # Simulates updating ONLY the project fields (no state/labels/assignees)
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "state": "open",
        "node_id": "I_kwHOA",
    }

    result = update_github_task_from_slack(
        owner="owner",
        repo="repo",
        issue_number=123,
        project_number=42,
        project_fields={"Status": "Done"},
    )

    assert result["ok"] is True

    # Assert we did a GET to fetch the node ID, not a PATCH
    args, kwargs = mock_github_client.request.call_args
    assert args[0] == "GET"
    assert args[1] == "repos/owner/repo/issues/123"

    mock_sync.assert_called_once()
    sync_args, _ = mock_sync.call_args
    assert sync_args[1] == "owner"
    assert sync_args[2] == 42
    assert sync_args[3] == "I_kwHOA"
    assert sync_args[4] == {"Status": "Done"}


def test_update_github_task_empty_payload(mock_github_client: MagicMock) -> None:
    result = update_github_task_from_slack(
        owner="owner",
        repo="repo",
        issue_number=123,
    )
    assert result["ok"] is False
    assert "No update fields provided" in result["error"]
    mock_github_client.request.assert_not_called()


# ---------------------------------------------------------------------------
# Close Tests
# ---------------------------------------------------------------------------


def test_close_github_task(mock_github_client: MagicMock) -> None:
    mock_github_client.request.return_value = {
        "number": 123,
        "html_url": "https://github.com/owner/repo/issues/123",
        "state": "closed",
    }

    result = close_github_task_from_slack(
        owner="owner",
        repo="repo",
        issue_number=123,
    )

    assert result["ok"] is True
    assert result["state"] == "closed"

    mock_github_client.request.assert_called_once()
    args, kwargs = mock_github_client.request.call_args
    assert args[0] == "PATCH"
    assert args[1] == "repos/owner/repo/issues/123"
    assert kwargs["body"]["state"] == "closed"


# ---------------------------------------------------------------------------
# Edge Case & Failure Scenarios
# ---------------------------------------------------------------------------


def test_extract_slack_url_edge_cases() -> None:
    from tools.slack_task_tool.tool import _extract_slack_url

    assert _extract_slack_url(None) == ""
    assert _extract_slack_url(MockContext(source_url=None)) == ""
    assert _extract_slack_url(MagicMock(spec=[])) == ""

    # Test explicit exception swallowing at the edge
    class FaultyContext:
        @property
        def session(self) -> Any:
            raise ValueError("Something violently broke")

    assert _extract_slack_url(FaultyContext()) == ""


def test_api_error_handling(mock_github_client: MagicMock) -> None:
    mock_github_client.request.side_effect = GitHubApiError(
        "Not Found", 404, "repos/owner/repo/issues"
    )

    result = create_github_task_from_slack(owner="owner", repo="repo", title="Fix it")

    assert result["ok"] is False
    assert "Not Found" in result["error"]


def test_internal_client_error_handling(mock_github_client: MagicMock) -> None:
    # Simulates missing configuration, missing tokens, or urllib transport crashes
    mock_github_client.request.side_effect = ValueError("Missing credentials")

    result = create_github_task_from_slack(owner="owner", repo="repo", title="Fix it")

    assert result["ok"] is False
    assert "Internal client error: Missing credentials" in result["error"]


def test_unexpected_response_type(mock_github_client: MagicMock) -> None:
    # Simulates GitHub acting up and returning a list instead of a dict
    mock_github_client.request.return_value = [{"number": 123}]

    result = create_github_task_from_slack(owner="owner", repo="repo", title="Fix it")

    assert result["ok"] is False
    assert "Unexpected GitHub API response type" in result["error"]
