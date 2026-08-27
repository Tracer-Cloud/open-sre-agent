"""Workflow contracts for the ninety-second pull-request execution gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load(_ROOT.joinpath(".github", "workflows", name).read_text(encoding="utf-8"))


def _action(name: str) -> dict[str, Any]:
    return yaml.safe_load(
        _ROOT.joinpath(".github", "actions", name, "action.yml").read_text(encoding="utf-8")
    )


def test_full_codeql_is_post_merge_and_pr_profile_is_manual() -> None:
    workflow = _workflow("codeql.yml")
    triggers = workflow[True]

    assert "pull_request" not in triggers
    assert {"push", "schedule", "workflow_dispatch"} <= set(triggers)

    jobs = workflow["jobs"]
    full = jobs["analyze-full"]
    assert full["strategy"]["matrix"]["language"] == [
        "python",
        "javascript-typescript",
    ]
    full_init = next(step for step in full["steps"] if step.get("name") == "Initialize CodeQL")
    assert full_init["with"]["queries"] == "security-and-quality"

    benchmark = jobs["analyze-pr-benchmark"]
    benchmark_init = next(
        step for step in benchmark["steps"] if step.get("name") == "Initialize CodeQL"
    )
    assert benchmark_init["with"]["config-file"] == ".github/codeql/codeql-pr-config.yml"
    assert "queries" not in benchmark_init["with"]


def test_heavy_test_suites_have_three_duration_balanced_groups() -> None:
    test_job = _workflow("ci.yml")["jobs"]["test"]
    entries = test_job["strategy"]["matrix"]["include"]

    assert test_job["strategy"]["max-parallel"] == 11
    for base in ("tools-runtime", "cli-runtime", "integrations-and-misc"):
        groups = [entry for entry in entries if entry["shard"].startswith(f"{base}-")]
        assert [entry["shard"] for entry in groups] == [
            f"{base}-1",
            f"{base}-2",
            f"{base}-3",
        ]
        assert all("--ci-splits=3" in entry["split_args"] for entry in groups)

    run_step = next(step for step in test_job["steps"] if step.get("name") == "Run tests")
    assert "-p tests.ci_sharding" in run_step["run"]
    assert "--cov=config" not in run_step["run"]
    assert "github.event_name == 'push'" in run_step["env"]["PYTEST_COVERAGE_ARGS"]


def test_quality_jobs_start_in_parallel_and_gate_aggregates_them() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"]["pull-requests"] == "read"
    assert "changes" not in jobs
    assert "needs" not in jobs["quality-static"]
    assert "needs" not in jobs["quality-typecheck"]
    assert "needs" not in jobs["test"]
    assert "Restore mypy cache" in {step.get("name") for step in jobs["quality-typecheck"]["steps"]}
    assert set(jobs["ci-gate"]["needs"]) == {
        "quality-static",
        "quality-typecheck",
        "test",
        "coverage-report",
    }


def test_source_filter_defaults_to_running_ci_for_new_file_types() -> None:
    inputs = _action("detect-source")["runs"]["steps"][0]["with"]
    filters = yaml.safe_load(inputs["filters"])["source"]

    assert inputs["predicate-quantifier"] == "every"
    assert filters == ["**", "!**/*.md", "!**/*.mdx", "!docs/**"]
