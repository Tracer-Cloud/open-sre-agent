"""Tests for architecture issue tool models and return contract."""

from __future__ import annotations

from tools.architecture_issue_tool.models import (
    ArchitectureViolation,
    RefactorTask,
    ScanSummary,
    build_error_result,
    build_success_result,
)


def test_architecture_violation_to_dict() -> None:
    violation = ArchitectureViolation(
        id="v-1",
        kind="layer_import",
        severity="p0",
        title="core must not import integrations",
        evidence={
            "path": "core/llm/factory.py",
            "line": 92,
            "source_module": "core.llm.factory",
            "target_module": "integrations.llm_cli.registry",
        },
        fix_direction="Move shared code to platform/ or inject at startup.",
    )

    payload = violation.to_dict()

    assert payload["id"] == "v-1"
    assert payload["kind"] == "layer_import"
    assert payload["severity"] == "p0"
    assert payload["evidence"]["line"] == 92


def test_refactor_task_to_dict_omits_empty_issue_body() -> None:
    task = RefactorTask(
        task_id="t-1",
        title="Remove core -> integrations import",
        description="Refactor llm factory to avoid integrations import.",
        scope_files=["core/llm/factory.py"],
        acceptance_criteria=["make check-imports passes"],
        labels=["refactor", "maintainability"],
        related_violation_ids=["v-1"],
    )

    payload = task.to_dict()

    assert payload["task_id"] == "t-1"
    assert "suggested_issue_body" not in payload


def test_refactor_task_to_dict_includes_issue_body_when_set() -> None:
    task = RefactorTask(
        task_id="t-2",
        title="Split oversized module",
        description="Extract helpers from oversized file.",
        scope_files=["tools/foo.py"],
        acceptance_criteria=["file under 500 lines"],
        labels=["refactor"],
        related_violation_ids=["v-2"],
        suggested_issue_body="## Summary\nSplit tools/foo.py",
    )

    payload = task.to_dict()

    assert payload["suggested_issue_body"].startswith("## Summary")


def test_build_success_result_shape() -> None:
    violations = [
        ArchitectureViolation(
            id="v-1",
            kind="oversized_file",
            severity="p2",
            title="Oversized file",
            evidence={"path": "tools/foo.py", "line_count": 501, "threshold": 500},
            fix_direction="Extract sibling modules.",
        )
    ]
    tasks = [
        RefactorTask(
            task_id="t-1",
            title="Split tools/foo.py",
            description="Reduce file size.",
            scope_files=["tools/foo.py"],
            acceptance_criteria=["under 500 lines"],
            labels=["refactor"],
            related_violation_ids=["v-1"],
        )
    ]

    result = build_success_result(
        owner="Tracer-Cloud",
        repo="opensre",
        ref="main",
        violations=violations,
        refactor_tasks=tasks,
        workspace_root="/tmp/opensre-audit-abc",
    )

    assert result["source"] == "github"
    assert result["available"] is True
    assert result["owner"] == "Tracer-Cloud"
    assert result["repo"] == "opensre"
    assert result["ref"] == "main"
    assert result["workspace_root"] == "/tmp/opensre-audit-abc"
    assert result["scan_summary"]["violations"] == 1
    assert result["scan_summary"]["tasks"] == 1
    assert len(result["violations"]) == 1
    assert len(result["refactor_tasks"]) == 1
    assert result["side_effects"] == []


def test_build_error_result_shape() -> None:
    result = build_error_result(
        owner="Tracer-Cloud",
        repo="opensre",
        error="clone failed",
        warnings=["lint-imports not available"],
    )

    assert result["available"] is False
    assert result["error"] == "clone failed"
    assert result["violations"] == []
    assert result["refactor_tasks"] == []
    assert result["side_effects"] == []
    assert result["scan_summary"]["warnings"] == ["lint-imports not available"]


def test_scan_summary_to_dict() -> None:
    summary = ScanSummary(
        violations=3,
        tasks=2,
        warnings=["skipped layer checks"],
        categories_scanned=["oversized_file", "compatibility_shim"],
    )

    payload = summary.to_dict()

    assert payload["violations"] == 3
    assert payload["categories_scanned"] == ["oversized_file", "compatibility_shim"]
