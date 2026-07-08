"""Tests for architecture issue tool refactor task synthesis."""

from __future__ import annotations

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.refactor_tasks import build_refactor_tasks, dedupe_violations


def _violation(
    *,
    violation_id: str,
    kind: str,
    path: str = "",
    edge: str = "",
) -> ArchitectureViolation:
    evidence: dict[str, object] = {}
    if path:
        evidence["path"] = path
    if edge:
        evidence["edge"] = edge
    return ArchitectureViolation(
        id=violation_id,
        kind=kind,  # type: ignore[arg-type]
        severity="p2",
        title=f"{kind} finding",
        evidence=evidence,
        fix_direction="Fix it.",
    )


def test_dedupe_violations_keeps_first_of_same_kind_and_path() -> None:
    first = _violation(violation_id="v-1", kind="oversized_file", path="tools/foo.py")
    second = _violation(violation_id="v-2", kind="oversized_file", path="tools/foo.py")

    deduped = dedupe_violations([first, second])

    assert deduped == [first]


def test_build_refactor_tasks_emits_one_task_per_violation() -> None:
    violations = [
        _violation(violation_id="v-1", kind="layer_import", edge="core.a -> integrations.b"),
        _violation(violation_id="v-2", kind="oversized_file", path="tools/foo.py"),
    ]

    tasks = build_refactor_tasks(violations)

    assert len(tasks) == 2
    assert tasks[0].related_violation_ids == ["v-1"]
    assert tasks[1].related_violation_ids == ["v-2"]
    assert "refactor" in tasks[0].labels
    assert tasks[0].suggested_issue_body.startswith("## Summary")


def test_build_refactor_tasks_includes_acceptance_criteria() -> None:
    violation = _violation(violation_id="v-1", kind="compatibility_shim", path="pkg/__init__.py")

    tasks = build_refactor_tasks([violation])

    assert tasks[0].acceptance_criteria
    assert "Forwarding module removed" in tasks[0].acceptance_criteria[1]
