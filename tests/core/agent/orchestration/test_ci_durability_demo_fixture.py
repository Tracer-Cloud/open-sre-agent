"""Contract tests for the disposable CI durability demo fixture."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness.prompts.skills.loader import skills_dir


def _fixture_module() -> dict[str, Any]:
    path = skills_dir() / "github_ci_durability_demo" / "demo_fixture.py"
    return runpy.run_path(str(path))


def test_fixture_exposes_second_defect_only_after_first_job_passes() -> None:
    module = _fixture_module()
    workflow = str(module["_WORKFLOW"])

    assert "durability-stage-one:" in workflow
    assert "durability-stage-two:" in workflow
    assert "needs: durability-stage-one" in workflow
    assert "return attempt\n" in str(module["_STAGE_ONE"])
    assert 'service_slug("Open SRE") == "open-sre"' in str(module["_STAGE_TWO"])


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://github.com/Tracer-Cloud/opensre.git", "Tracer-Cloud/opensre"),
        ("git@github.com:Tracer-Cloud/opensre.git", "Tracer-Cloud/opensre"),
    ],
)
def test_repo_scope_accepts_common_github_remote_forms(origin: str, expected: str) -> None:
    module = _fixture_module()

    assert module["_repo_scope"](origin) == expected


def test_cleanup_state_rejects_non_demo_filename(tmp_path: Path) -> None:
    module = _fixture_module()
    state_file = tmp_path / "ordinary-state.json"
    state_file.write_text("{}", encoding="utf-8")

    with pytest.raises(module["DemoError"], match="not created by this demo"):
        module["_validated_state"](state_file)


def test_cleanup_continues_after_pr_failure_and_retains_retry_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_values = _fixture_module()
    cleanup = module_values["cleanup"]
    branch = "codex/ci-durability-demo-test"
    state_file = tmp_path / "opensre-ci-durability-demo-state-test.json"
    state_file.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def _validated_state(_path: Path) -> dict[str, Any]:
        return {
            "branch": branch,
            "pr_number": 123,
            "repo": "owner/repo",
            "repo_root": str(tmp_path),
            "worktree": str(tmp_path / "opensre-ci-durability-demo-worktree-test"),
        }

    def _run(*args: str, cwd: Path | None = None) -> str:
        _ = cwd
        calls.append(args)
        if args[:3] == ("gh", "pr", "view"):
            raise module_values["DemoError"]("simulated GitHub outage")
        if args[:3] == ("git", "ls-remote", "--heads"):
            return "remote branch exists"
        if args[:3] == ("git", "branch", "--list"):
            return branch
        return ""

    monkeypatch.setitem(cleanup.__globals__, "_validated_state", _validated_state)
    monkeypatch.setitem(cleanup.__globals__, "_run", _run)

    result = cleanup(state_file)

    assert result["ok"] is False
    assert result["state_file_deleted"] is False
    assert state_file.exists()
    assert ("git", "push", "origin", "--delete", branch) in calls
    assert ("git", "branch", "-D", branch) in calls
