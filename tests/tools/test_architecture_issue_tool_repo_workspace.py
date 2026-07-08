"""Tests for architecture issue tool repo workspace helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_sandbox_dir,
    cloned_github_repo,
    github_remote_url,
    resolve_scan_roots,
)


def test_github_remote_url() -> None:
    assert (
        github_remote_url("Tracer-Cloud", "opensre")
        == "https://github.com/Tracer-Cloud/opensre.git"
    )


def test_resolve_scan_roots_skips_tests_and_docs(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x() -> None: ...\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# docs\n", encoding="utf-8")

    roots = resolve_scan_roots(tmp_path)

    assert [path.name for path in roots] == ["core"]


def test_cloned_github_repo_uses_local_path_without_clone(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    marker.write_text("stay", encoding="utf-8")

    with cloned_github_repo("org", "repo", local_path=str(tmp_path)) as workspace:
        assert workspace.root == tmp_path.resolve()
        assert workspace.owner == "org"
        assert workspace.repo == "repo"
        assert marker.read_text(encoding="utf-8") == "stay"

    assert marker.exists()


def test_cloned_github_repo_local_path_must_exist(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with (
        pytest.raises(WorkspaceError, match="not a directory"),
        cloned_github_repo("org", "repo", local_path=str(missing)),
    ):
        pass


def test_cloned_github_repo_requires_owner_and_repo() -> None:
    with pytest.raises(WorkspaceError, match="owner and repo"), cloned_github_repo("", "repo"):
        pass


def test_architecture_sandbox_dir_is_under_project_temp() -> None:
    sandbox = architecture_sandbox_dir()
    assert sandbox.name == "sandbox"
    assert sandbox.parent.name == "opensre"
    assert sandbox.parent.parent.name == ".temp"


@patch("tools.architecture_issue_tool.repo_workspace._shallow_clone")
@patch("tools.architecture_issue_tool.repo_workspace._remote_default_branch", return_value="main")
@patch("tools.architecture_issue_tool.repo_workspace.shutil.rmtree")
@patch("tools.architecture_issue_tool.repo_workspace._prepare_architecture_sandbox")
def test_cloned_github_repo_clones_and_cleans_up(
    mock_prepare_sandbox,
    mock_rmtree,
    mock_default_branch,
    mock_shallow_clone,
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / ".temp" / "opensre" / "sandbox"
    sandbox.mkdir(parents=True)
    mock_prepare_sandbox.return_value = sandbox

    def _clone(**kwargs: object) -> None:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.mkdir(parents=True, exist_ok=True)

    mock_shallow_clone.side_effect = _clone

    with cloned_github_repo("Tracer-Cloud", "opensre", token="ghp_test") as workspace:
        assert workspace.ref == "main"
        assert workspace.root == sandbox
        mock_default_branch.assert_called_once()
        mock_shallow_clone.assert_called_once()

    mock_rmtree.assert_called_once_with(sandbox, ignore_errors=True)


@patch("tools.architecture_issue_tool.repo_workspace._shallow_clone")
@patch("tools.architecture_issue_tool.repo_workspace.shutil.rmtree")
@patch("tools.architecture_issue_tool.repo_workspace._prepare_architecture_sandbox")
def test_cloned_github_repo_cleans_up_on_clone_failure(
    mock_prepare_sandbox,
    mock_rmtree,
    mock_shallow_clone,
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / ".temp" / "opensre" / "sandbox"
    sandbox.mkdir(parents=True)
    mock_prepare_sandbox.return_value = sandbox
    mock_shallow_clone.side_effect = WorkspaceError("git clone failed")

    with (
        pytest.raises(WorkspaceError, match="git clone failed"),
        cloned_github_repo("Tracer-Cloud", "opensre", ref="main"),
    ):
        pass

    mock_rmtree.assert_called_once_with(sandbox, ignore_errors=True)
