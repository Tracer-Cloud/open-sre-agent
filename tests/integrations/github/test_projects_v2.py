"""Tests for GitHub Projects V2 GraphQL operations."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from integrations.github.client import GitHubApiError
from integrations.github.projects_v2 import (
    add_issue_to_project_v2,
    fetch_project_fields,
    resolve_project_node_id,
    sync_project_fields,
    update_project_v2_field,
)


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    return client


def test_resolve_project_node_id_org(mock_client: MagicMock) -> None:
    mock_client.graphql.return_value = {
        "data": {"organization": {"projectV2": {"id": "PN_kwHOA"}}, "user": None}
    }
    node_id = resolve_project_node_id(mock_client, "acme", 1)
    assert node_id == "PN_kwHOA"


def test_resolve_project_node_id_user(mock_client: MagicMock) -> None:
    mock_client.graphql.return_value = {
        "data": {"organization": None, "user": {"projectV2": {"id": "PN_user"}}}
    }
    node_id = resolve_project_node_id(mock_client, "alice", 2)
    assert node_id == "PN_user"


def test_resolve_project_node_id_error(mock_client: MagicMock) -> None:
    mock_client.graphql.side_effect = GitHubApiError("Not Found")
    assert resolve_project_node_id(mock_client, "alice", 2) is None


def test_fetch_project_fields(mock_client: MagicMock) -> None:
    mock_client.graphql.return_value = {
        "data": {
            "node": {
                "fields": {
                    "nodes": [
                        {
                            "id": "F_1",
                            "name": "Status",
                            "dataType": "SINGLE_SELECT",
                            "options": [{"id": "O_1", "name": "Done"}],
                        },
                        {"id": "F_2", "name": "Title", "dataType": "TEXT"},
                    ]
                }
            }
        }
    }
    fields = fetch_project_fields(mock_client, "PN_1")
    assert len(fields) == 2
    assert fields[0]["name"] == "Status"


def test_add_issue_to_project_v2(mock_client: MagicMock) -> None:
    mock_client.graphql.return_value = {
        "data": {"addProjectV2ItemById": {"item": {"id": "PI_123"}}}
    }
    item_id = add_issue_to_project_v2(mock_client, "PN_1", "I_1")
    assert item_id == "PI_123"


def test_update_project_v2_field(mock_client: MagicMock) -> None:
    update_project_v2_field(mock_client, "PN_1", "PI_1", "F_1", {"text": "hello"})
    mock_client.graphql.assert_called_once()
    args, kwargs = mock_client.graphql.call_args
    assert "updateProjectV2ItemFieldValue" in args[0]
    assert args[1]["value"] == {"text": "hello"}


def test_sync_project_fields(mock_client: MagicMock) -> None:
    mock_client.graphql.side_effect = [
        # 1. Resolve project
        {"data": {"organization": {"projectV2": {"id": "PN_1"}}}},
        # 2. Add issue to project
        {"data": {"addProjectV2ItemById": {"item": {"id": "PI_1"}}}},
        # 3. Fetch fields
        {
            "data": {
                "node": {
                    "fields": {
                        "nodes": [
                            {
                                "id": "F_status",
                                "name": "Status",
                                "dataType": "SINGLE_SELECT",
                                "options": [{"id": "O_done", "name": "Done"}],
                            },
                            {"id": "F_text", "name": "Notes", "dataType": "TEXT"},
                            {
                                "id": "F_sprint",
                                "name": "Sprint",
                                "dataType": "ITERATION",
                                "configuration": {
                                    "iterations": [{"id": "I_1", "title": "Sprint 1"}]
                                },
                            },
                        ]
                    }
                }
            }
        },
        # 4, 5, 6. Updates
        {},
        {},
        {},
    ]

    success = sync_project_fields(
        mock_client,
        owner="org",
        project_number=1,
        issue_node_id="I_1",
        project_fields={"Status": "Done", "Notes": "Hello", "Sprint": "Sprint 1"},
    )

    assert success is True
    # 3 updates + 1 resolve + 1 add + 1 fetch = 6 calls
    assert mock_client.graphql.call_count == 6
