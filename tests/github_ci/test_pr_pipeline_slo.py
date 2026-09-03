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
    full_analyze = next(
        step for step in full["steps"] if step.get("name") == "Perform CodeQL Analysis"
    )
    assert full_analyze["with"]["category"] == "/language:${{ matrix.language }}"

    benchmark = jobs["analyze-pr-benchmark"]
    benchmark_init = next(
        step for step in benchmark["steps"] if step.get("name") == "Initialize CodeQL"
    )
    assert benchmark_init["with"]["config-file"] == ".github/codeql/codeql-pr-config.yml"
    assert "queries" not in benchmark_init["with"]
    benchmark_analyze = next(
        step for step in benchmark["steps"] if step.get("name") == "Perform CodeQL Analysis"
    )
    assert benchmark_analyze["with"]["category"] == "/language:python/pr-fast"


def test_heavy_test_suites_are_duration_balanced_with_measured_headroom() -> None:
    test_job = _workflow("ci.yml")["jobs"]["test"]
    entries = test_job["strategy"]["matrix"]["include"]

    assert test_job["strategy"]["max-parallel"] == 23
    expected_splits = {
        "tools-runtime": 6,
        "cli-runtime": 6,
        "integrations-and-misc": 6,
    }
    for base, splits in expected_splits.items():
        groups = [entry for entry in entries if entry["shard"].startswith(f"{base}-")]
        assert [entry["shard"] for entry in groups] == [
            f"{base}-{group}" for group in range(1, splits + 1)
        ]
        assert all(f"--ci-splits={splits}" in entry["split_args"] for entry in groups)

    live_agent = next(entry for entry in entries if entry["shard"] == "cli-live-agent")
    assert live_agent["llm_provider"] == "openai"
    assert live_agent["pytest_paths"].split() == [
        "tests/tools/selection",
    ]
    tool_groups = [entry for entry in entries if entry["shard"].startswith("tools-runtime-")]
    assert all(
        "--ignore=tests/tools/selection" in entry["extra_pytest_args"] for entry in tool_groups
    )
    cli_groups = [entry for entry in entries if entry["shard"].startswith("cli-runtime-")]
    assert all(
        "--ignore=tests/cli/test_smoke.py" in entry["extra_pytest_args"] for entry in cli_groups
    )

    smoke_groups = {
        entry["shard"]: entry for entry in entries if entry["shard"].startswith("cli-smoke-")
    }
    assert set(smoke_groups) == {"cli-smoke-1", "cli-smoke-2"}
    assert all(
        entry["pytest_paths"] == "tests/cli/test_smoke.py" for entry in smoke_groups.values()
    )
    first_selector = smoke_groups["cli-smoke-1"]["extra_pytest_args"].removeprefix("-k ")
    second_selector = smoke_groups["cli-smoke-2"]["extra_pytest_args"].removeprefix("-k ")
    first_expression = first_selector.removeprefix("'").removesuffix("'")
    assert second_selector == "'not (" + first_expression + ")'"

    run_step = next(step for step in test_job["steps"] if step.get("name") == "Run tests")
    assert "-p tests.ci_sharding" in run_step["run"]
    assert "--cov=config" not in run_step["run"]
    assert "github.event_name == 'push'" in run_step["env"]["PYTEST_COVERAGE_ARGS"]


def test_fork_pull_requests_skip_only_the_live_agent_shard() -> None:
    test_job = _workflow("ci.yml")["jobs"]["test"]
    run_step = next(step for step in test_job["steps"] if step.get("name") == "Run tests")
    condition = " ".join(run_step["if"].split())

    assert condition == (
        "(steps.changes.outputs.source == 'true' || github.event_name == 'push') && "
        "(matrix.shard != 'cli-live-agent' || github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.fork == false)"
    )


def test_quality_jobs_start_in_parallel_and_gate_aggregates_them() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"]["pull-requests"] == "read"
    assert "changes" not in jobs
    assert "needs" not in jobs["quality-static"]
    assert "needs" not in jobs["quality-typecheck"]
    assert "needs" not in jobs["test"]
    assert "needs" not in jobs["session-store-locked"]
    assert "Restore mypy cache" in {step.get("name") for step in jobs["quality-typecheck"]["steps"]}
    assert "Verify typed tool contracts" in {
        step.get("name") for step in jobs["quality-typecheck"]["steps"]
    }
    assert "Verify tool registry index" in {
        step.get("name") for step in jobs["quality-static"]["steps"]
    }
    tool_groups = [
        entry
        for entry in jobs["test"]["strategy"]["matrix"]["include"]
        if entry["shard"].startswith("tools-runtime-")
    ]
    assert all(
        "--ignore=tests/core/tool/test_contracts.py" in entry["extra_pytest_args"]
        for entry in tool_groups
    )
    assert all(
        "--ignore=tests/tools/test_registry_index.py" in entry["extra_pytest_args"]
        for entry in tool_groups
    )
    assert set(jobs["ci-gate"]["needs"]) == {
        "quality-static",
        "quality-typecheck",
        "test",
        "coverage-report",
        "session-store-locked",
    }


def test_source_filter_defaults_to_running_ci_for_new_file_types() -> None:
    inputs = _action("detect-source")["runs"]["steps"][0]["with"]
    filters = yaml.safe_load(inputs["filters"])["source"]

    assert inputs["predicate-quantifier"] == "every"
    assert filters == ["**", "!**/*.md", "!**/*.mdx", "!docs/**"]


def test_session_store_locked_job_contracts() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]
    locked_job = jobs["session-store-locked"]

    assert (
        locked_job["outputs"]["session_persistence"]
        == "${{ steps.changes.outputs.session_persistence }}"
    )
    change_step = next(step for step in locked_job["steps"] if step.get("id") == "changes")
    filters = yaml.safe_load(change_step["with"]["filters"])
    assert filters["session_persistence"] == ["core/agent_harness/session/persistence/**"]
    # Without every, the bare "**" in `source` always matches and the
    # negations (!*.md etc.) never take effect — see detect-source/action.yml.
    assert change_step["with"]["predicate-quantifier"] == "every"

    gate_run = next(
        step["run"]
        for step in jobs["ci-gate"]["steps"]
        if step.get("name") == "Require green upstream jobs"
    )
    assert (
        "session_persistence_changed='${{ needs.session-store-locked.outputs.session_persistence }}'"
        in gate_run
    )
    assert 'if [ "$session_persistence_changed" = "true" ]; then' in gate_run
