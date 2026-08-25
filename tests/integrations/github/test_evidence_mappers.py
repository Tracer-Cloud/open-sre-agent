from __future__ import annotations

from typing import Any

from integrations.github.tools.actions import _map_get_github_actions_step_log
from integrations.github.tools.file_contents import _map_get_github_file_contents
from integrations.github.tools.git_deploy_timeline_tool import _map_get_git_deploy_timeline
from integrations.github.tools.repository import _map_get_github_repository
from integrations.github.tools.repository_tree import _map_get_github_repository_tree
from integrations.github.tools.work_status_report_tool import _map_generate_work_status_report


def test_map_get_github_actions_step_log() -> None:
    evidence: dict[str, Any] = {}
    output = {"log_text": "Error: missing semicolon", "returned_lines": 1}
    _map_get_github_actions_step_log(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_actions_step_log"
    assert entries[0]["summary"] == "1 lines"


def test_map_get_github_file_contents() -> None:
    evidence: dict[str, Any] = {}
    output = {"file": {"content": "def main(): pass"}, "path": "src/main.py"}
    _map_get_github_file_contents(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_file_contents"
    assert entries[0]["summary"] == "File: src/main.py"


def test_map_get_github_repository() -> None:
    evidence: dict[str, Any] = {}
    output = {"repository": {"stargazers_count": 42}, "owner": "Tracer-Cloud", "repo": "opensre"}
    _map_get_github_repository(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_repository"
    assert entries[0]["summary"] == "Tracer-Cloud/opensre"


def test_map_get_github_repository_tree() -> None:
    evidence: dict[str, Any] = {}
    output = {"tree": {"tree": [{"path": "README.md"}, {"path": "src"}]}}
    _map_get_github_repository_tree(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_repository_tree"
    assert entries[0]["summary"] == "2 items"


def test_map_generate_work_status_report() -> None:
    evidence: dict[str, Any] = {}
    output = {"slack_text": "Here is your report."}
    _map_generate_work_status_report(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "generate_work_status_report"
    assert entries[0]["summary"] == "Engineering work status report"


def test_map_get_git_deploy_timeline() -> None:
    evidence: dict[str, Any] = {}
    output = {"commits": [{"commit": "abc"}, {"commit": "def"}]}
    _map_get_git_deploy_timeline(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_git_deploy_timeline"
    assert entries[0]["summary"] == "2 deploys"
