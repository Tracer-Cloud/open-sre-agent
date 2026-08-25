"""Evidence mapper tests for the Bitbucket tools."""

from integrations.bitbucket.tools.bitbucket_commits_tool import _map_list_bitbucket_commits
from integrations.bitbucket.tools.bitbucket_file_contents_tool import (
    _map_get_bitbucket_file_contents,
)
from integrations.bitbucket.tools.bitbucket_search_code_tool import _map_search_bitbucket_code
from tools.registry import get_registered_tool


def test_file_contents_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    content = "print('hello')"
    output = {
        "source": "bitbucket",
        "available": True,
        "repo": "acme/backend-service",
        "path": "src/main.py",
        "ref": "main",
        "content": content,
        "truncated": False,
    }

    _map_get_bitbucket_file_contents(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_bitbucket_file_contents",
            "label": "Bitbucket File Contents",
            "summary": f"{len(content)} chars from src/main.py",
            "url": None,
            "snippet": None,
        }
    ]


def test_file_contents_mapper_notes_truncation() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": True,
        "repo": "acme/backend-service",
        "path": "src/main.py",
        "ref": "HEAD",
        "content": "x" * 10000,
        "truncated": True,
    }

    _map_get_bitbucket_file_contents(evidence, output, {})

    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["summary"] == "10000 chars from src/main.py (truncated)"


def test_file_contents_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": False,
        "error": "Bitbucket integration is not configured.",
        "file": {},
    }

    _map_get_bitbucket_file_contents(evidence, output, {})

    assert "catalog_entries" not in evidence


def test_commits_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": True,
        "repo": "acme/backend-service",
        "total_returned": 1,
        "commits": [
            {
                "hash": "abc123def456",
                "message": "Fix flaky test",
                "author": "Jane Doe",
                "date": "2026-04-28T10:00:00Z",
            }
        ],
    }

    _map_list_bitbucket_commits(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "list_bitbucket_commits",
            "label": "Bitbucket Commits",
            "summary": "1 commits",
            "url": None,
            "snippet": None,
        }
    ]


def test_commits_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": False,
        "error": "Bitbucket integration is not configured.",
        "commits": [],
    }

    _map_list_bitbucket_commits(evidence, output, {})

    assert "catalog_entries" not in evidence


def test_search_code_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": True,
        "query": "error OR exception",
        "total_returned": 1,
        "results": [
            {
                "path": "src/main.py",
                "repo": "acme/backend-service",
                "content_matches": 2,
            }
        ],
    }

    _map_search_bitbucket_code(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "search_bitbucket_code",
            "label": "Bitbucket Code Search",
            "summary": "1 matches",
            "url": None,
            "snippet": None,
        }
    ]


def test_search_code_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, object] = {}
    output = {
        "source": "bitbucket",
        "available": False,
        "error": "Bitbucket integration is not configured.",
        "results": [],
    }

    _map_search_bitbucket_code(evidence, output, {})

    assert "catalog_entries" not in evidence


def test_registered_tools_carry_their_mappers() -> None:
    file_contents_tool = get_registered_tool("get_bitbucket_file_contents")
    commits_tool = get_registered_tool("list_bitbucket_commits")
    search_code_tool = get_registered_tool("search_bitbucket_code")

    assert file_contents_tool is not None
    assert commits_tool is not None
    assert search_code_tool is not None
    assert file_contents_tool.evidence_mapper is _map_get_bitbucket_file_contents
    assert commits_tool.evidence_mapper is _map_list_bitbucket_commits
    assert search_code_tool.evidence_mapper is _map_search_bitbucket_code
