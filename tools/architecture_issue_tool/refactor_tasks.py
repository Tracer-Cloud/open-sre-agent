"""Convert architecture violations into atomic refactor task suggestions."""

from __future__ import annotations

import hashlib

from tools.architecture_issue_tool.models import (
    DEFAULT_REFACTOR_LABELS,
    ArchitectureViolation,
    RefactorTask,
)


def _dedupe_key(violation: ArchitectureViolation) -> tuple[str, str]:
    evidence = violation.evidence
    primary = (
        str(evidence.get("path", ""))
        or str(evidence.get("edge", ""))
        or str(evidence.get("source_module", ""))
        or violation.id
    )
    return violation.kind, primary


def dedupe_violations(violations: list[ArchitectureViolation]) -> list[ArchitectureViolation]:
    """Drop duplicate findings that share kind and primary evidence."""
    seen: set[tuple[str, str]] = set()
    unique: list[ArchitectureViolation] = []

    for violation in violations:
        key = _dedupe_key(violation)
        if key in seen:
            continue
        seen.add(key)
        unique.append(violation)

    return unique


def _task_id(violation: ArchitectureViolation) -> str:
    digest = hashlib.sha256(f"task:{violation.id}".encode()).hexdigest()
    return f"t-{digest[:12]}"


def _issue_body(violation: ArchitectureViolation, task: RefactorTask) -> str:
    evidence_lines = "\n".join(f"- {key}: {value}" for key, value in violation.evidence.items())
    acceptance = "\n".join(f"- [ ] {item}" for item in task.acceptance_criteria)
    return (
        f"## Summary\n{task.description}\n\n"
        f"## Violation\n{violation.title}\n\n"
        f"## Evidence\n{evidence_lines}\n\n"
        f"## Fix direction\n{violation.fix_direction}\n\n"
        f"## Acceptance criteria\n{acceptance}\n"
    )


def _task_for_violation(violation: ArchitectureViolation) -> RefactorTask:
    scope_files: list[str] = []
    if path := violation.evidence.get("path"):
        scope_files.append(str(path))
    elif source := violation.evidence.get("source_module"):
        module_path = str(source).replace(".", "/") + ".py"
        scope_files.append(module_path)

    if violation.kind in ("layer_import", "direct_import"):
        edge = str(violation.evidence.get("edge", violation.title))
        title = f"Fix import edge: {edge}"
        description = (
            f"Refactor {edge} so the repository respects its layer contract. "
            f"{violation.fix_direction}"
        )
        acceptance = ["make check-imports passes", "No new direct or layer import violations"]
    elif violation.kind == "oversized_file":
        path = str(violation.evidence.get("path", "target file"))
        threshold = violation.evidence.get("threshold", 500)
        title = f"Split oversized file: {path}"
        description = f"{path} exceeds the architecture scan threshold. {violation.fix_direction}"
        acceptance = [f"{path} is at or below {threshold} non-blank code lines"]
    elif violation.kind == "compatibility_shim":
        path = str(violation.evidence.get("path", "shim module"))
        title = f"Remove compatibility shim: {path}"
        description = (
            f"{path} appears to be a compatibility-only forwarding module. "
            f"{violation.fix_direction}"
        )
        acceptance = ["Callers import the canonical module path", "Forwarding module removed"]
    else:
        path = str(violation.evidence.get("path", violation.title))
        title = f"Relocate misplaced module: {path}"
        description = violation.fix_direction
        acceptance = ["Module lives in the canonical package per tool-placement policy"]

    task = RefactorTask(
        task_id=_task_id(violation),
        title=title,
        description=description,
        scope_files=scope_files,
        acceptance_criteria=acceptance,
        labels=list(DEFAULT_REFACTOR_LABELS),
        related_violation_ids=[violation.id],
    )
    return RefactorTask(
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        scope_files=task.scope_files,
        acceptance_criteria=task.acceptance_criteria,
        labels=task.labels,
        related_violation_ids=task.related_violation_ids,
        suggested_issue_body=_issue_body(violation, task),
    )


def build_refactor_tasks(violations: list[ArchitectureViolation]) -> list[RefactorTask]:
    """Build one atomic refactor task per violation."""
    return [_task_for_violation(violation) for violation in violations]
