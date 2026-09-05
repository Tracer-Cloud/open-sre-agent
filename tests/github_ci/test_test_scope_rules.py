"""Regression tests for changed-path test target selection."""

from __future__ import annotations

import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parents[2] / ".github" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from test_scope_rules import classify  # noqa: E402


def test_scheduler_source_changes_run_scheduler_suite() -> None:
    escalate, targets, areas = classify(["infrastructure/scheduling/scheduler/runner.py"])

    assert escalate is False
    assert targets == ["tests/scheduler/"]
    assert areas == ["infrastructure/scheduling/"]


def test_scheduler_source_with_specific_test_still_runs_scheduler_suite() -> None:
    escalate, targets, areas = classify(
        [
            "infrastructure/scheduling/scheduler/storage/task_store.py",
            "tests/scheduler/test_task_store.py",
        ]
    )

    assert escalate is False
    assert targets == ["tests/scheduler/", "tests/scheduler/test_task_store.py"]
    assert areas == ["infrastructure/scheduling/"]
