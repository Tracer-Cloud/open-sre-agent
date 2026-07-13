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
        title="Remove compatibility shim",
        description="Delete forwarding module.",
        scope_files=["tools/foo.py"],
        acceptance_criteria=["Forwarding module removed"],
        labels=["refactor"],
        related_violation_ids=["v-2"],
        suggested_issue_body="## Summary\nRemove tools/foo.py shim",
    )

    payload = task.to_dict()

    assert payload["suggested_issue_body"].startswith("## Summary")


def test_build_success_result_shape() -> None:
    violations = [
        ArchitectureViolation(
            id="v-1",
            kind="misplaced_module",
            severity="p2",
            title="Misplaced module",
            evidence={"path": "tools/foo.py", "pattern": "known_vendor_tool"},
            fix_direction="Move to integrations.",
        )
    ]
    tasks = [
        RefactorTask(
            task_id="t-1",
            title="Relocate tools/foo.py",
            description="Move module.",
            scope_files=["tools/foo.py"],
            acceptance_criteria=["Module lives in the canonical package"],
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
        categories_scanned=["misplaced_module", "layer_import"],
        categories_skipped=["layer_import"],
        severity_counts={"p0": 1, "p1": 1, "p2": 1},
        kind_counts={"layer_import": 1, "misplaced_module": 2},
        hotspots=[{"area": "core", "count": 2, "share": 0.6667}],
        coverage_complete=False,
    )

    payload = summary.to_dict()

    assert payload["violations"] == 3
    assert payload["categories_scanned"] == ["misplaced_module", "layer_import"]
    assert payload["categories_skipped"] == ["layer_import"]
    assert payload["severity_counts"] == {"p0": 1, "p1": 1, "p2": 1}
    assert payload["kind_counts"] == {"layer_import": 1, "misplaced_module": 2}
    assert payload["hotspots"] == [{"area": "core", "count": 2, "share": 0.6667}]
    assert payload["coverage_complete"] is False


def test_build_error_result_marks_incomplete_coverage() -> None:
    result = build_error_result(
        owner="Tracer-Cloud",
        repo="opensre",
        error="clone failed",
        warnings=["lint-imports not available"],
    )

    assert result["scan_summary"]["coverage_complete"] is False
