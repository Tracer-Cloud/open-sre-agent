from __future__ import annotations

from typing import Any

from integrations.github.tools.actions import (
    _map_get_github_actions_step_log,
    _map_list_github_actions_active_runs,
    _map_list_github_actions_run_jobs,
    _map_list_github_actions_workflow_runs,
)
from integrations.github.tools.commits import _map_list_github_commits
from integrations.github.tools.file_contents import _map_get_github_file_contents
from integrations.github.tools.git_deploy_timeline_tool import _map_get_git_deploy_timeline
from integrations.github.tools.repository import _map_get_github_repository
from integrations.github.tools.repository_tree import _map_get_github_repository_tree
from integrations.github.tools.stargazers import _map_get_github_star_history
from integrations.github.tools.work_status import _map_list_github_security_alerts
from integrations.github.tools.work_status_report_tool import _map_generate_work_status_report


def test_map_get_github_actions_step_log() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_github_actions_step_log(
        evidence_single, {"log_text": "Error", "returned_lines": 1}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_actions_step_log"
    assert entries[0]["summary"] == "1 line"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_actions_step_log(
        evidence_plural, {"log_text": "Error\nTraceback", "returned_lines": 2}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 lines"


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
    evidence_single: dict[str, Any] = {}
    _map_get_github_repository_tree(
        evidence_single, {"tree": {"tree": [{"path": "README.md"}]}}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_repository_tree"
    assert entries[0]["summary"] == "1 item"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_repository_tree(
        evidence_plural, {"tree": {"tree": [{"path": "README.md"}, {"path": "src"}]}}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 items"


def test_map_generate_work_status_report() -> None:
    evidence: dict[str, Any] = {}
    output = {"slack_text": "Here is your report."}
    _map_generate_work_status_report(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "generate_work_status_report"
    assert entries[0]["summary"] == "Engineering work status report"


def test_map_get_git_deploy_timeline() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_git_deploy_timeline(evidence_single, {"commits": [{"commit": "abc"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_git_deploy_timeline"
    assert entries[0]["summary"] == "1 deploy"

    evidence_plural: dict[str, Any] = {}
    _map_get_git_deploy_timeline(
        evidence_plural, {"commits": [{"commit": "abc"}, {"commit": "def"}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 deploys"


def test_map_get_github_star_history() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_github_star_history(
        evidence_single, {"daily": [{"date": "2026-08-01", "stars": 5}]}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_star_history"
    assert entries[0]["summary"] == "1 day recorded"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_star_history(evidence_plural, {"daily": [{"date": "1"}, {"date": "2"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 days recorded"


def test_map_list_github_actions_workflow_runs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_workflow_runs(evidence_single, {"workflow_runs": [{"id": 42}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_workflow_runs"
    assert entries[0]["summary"] == "1 run"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_workflow_runs(
        evidence_plural, {"workflow_runs": [{"id": 42}, {"id": 43}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 runs"


def test_map_list_github_actions_active_runs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_active_runs(evidence_single, {"workflow_runs": [{"id": 1}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_active_runs"
    assert entries[0]["summary"] == "1 run"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_active_runs(
        evidence_plural, {"workflow_runs": [{"id": 1}, {"id": 2}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 runs"


def test_map_list_github_actions_run_jobs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_run_jobs(evidence_single, {"jobs": [{"id": 101}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_run_jobs"
    assert entries[0]["summary"] == "1 job"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_run_jobs(evidence_plural, {"jobs": [{"id": 1}, {"id": 2}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 jobs"


def test_map_list_github_commits() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_commits(evidence_single, {"commits": [{"sha": "abc"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_commits"
    assert entries[0]["summary"] == "1 commit"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_commits(evidence_plural, {"commits": [{"sha": "abc"}, {"sha": "def"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 commits"


def test_map_list_github_security_alerts() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_security_alerts(evidence_single, {"alerts": [{"id": "alert1"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_security_alerts"
    assert entries[0]["summary"] == "1 alert"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_security_alerts(evidence_plural, {"alerts": [{"id": "a"}, {"id": "b"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 alerts"
