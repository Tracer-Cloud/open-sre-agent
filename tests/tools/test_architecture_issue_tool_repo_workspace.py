"""Tests for architecture issue tool repo workspace helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
    cloned_github_repo,
    github_remote_url,
)


def test_github_remote_url() -> None:
    assert (
        github_remote_url("Tracer-Cloud", "opensre")
        == "https://github.com/Tracer-Cloud/opensre.git"
    )


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


def test_architecture_workspace_dir_is_under_opensre_tmp() -> None:
    from config.constants.paths import OPENSRE_TMP_DIR

    workspace = architecture_workspace_dir()
    assert workspace == OPENSRE_TMP_DIR / "workspace"


def test_cleanup_architecture_workspace_refuses_outside_path(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="outside"):
        cleanup_architecture_workspace(path=tmp_path)


def test_cleanup_architecture_workspace_surfaces_rmtree_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stale.txt").write_text("x", encoding="utf-8")

    with (
        patch(
            "tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
            return_value=workspace,
        ),
        patch(
            "tools.architecture_issue_tool.repo_workspace.shutil.rmtree",
            side_effect=OSError("Permission denied"),
        ),
        pytest.raises(WorkspaceError, match="could not remove"),
    ):
        cleanup_architecture_workspace()


def test_prepare_architecture_workspace_surfaces_rmtree_errors(tmp_path: Path) -> None:
    from tools.architecture_issue_tool.repo_workspace import prepare_architecture_workspace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stale.txt").write_text("x", encoding="utf-8")

    with (
        patch(
            "tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
            return_value=workspace,
        ),
        patch(
            "tools.architecture_issue_tool.repo_workspace.shutil.rmtree",
            side_effect=OSError("Directory not empty"),
        ),
        pytest.raises(WorkspaceError, match="could not remove"),
    ):
        prepare_architecture_workspace()


def test_cleanup_architecture_workspace_fails_if_path_still_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def _noop_rmtree(path: object, *args: object, **kwargs: object) -> None:
        return None

    with (
        patch(
            "tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
            return_value=workspace,
        ),
        patch(
            "tools.architecture_issue_tool.repo_workspace.shutil.rmtree",
            side_effect=_noop_rmtree,
        ),
        pytest.raises(WorkspaceError, match="still exists"),
    ):
        cleanup_architecture_workspace()


@patch("tools.architecture_issue_tool.repo_workspace._shallow_clone")
@patch("tools.architecture_issue_tool.repo_workspace._remote_default_branch", return_value="main")
@patch("tools.architecture_issue_tool.repo_workspace.prepare_architecture_workspace")
def test_cloned_github_repo_clones_and_cleans_up(
    mock_prepare,
    mock_default_branch,
    mock_shallow_clone,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "opensre" / "workspace"
    workspace.mkdir(parents=True)
    mock_prepare.return_value = workspace

    def _clone(**kwargs: object) -> None:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "README.md").write_text("ok\n", encoding="utf-8")

    mock_shallow_clone.side_effect = _clone

    with (
        patch(
            "tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
            return_value=workspace,
        ),
        cloned_github_repo("Tracer-Cloud", "opensre", token="ghp_test") as result,
    ):
        assert result.ref == "main"
        assert result.root == workspace
        mock_default_branch.assert_called_once()
        mock_shallow_clone.assert_called_once()
        assert workspace.exists()

    assert not workspace.exists()


@patch("tools.architecture_issue_tool.repo_workspace._shallow_clone")
@patch("tools.architecture_issue_tool.repo_workspace.prepare_architecture_workspace")
def test_clone_github_repo_cleans_up_on_clone_failure(
    mock_prepare,
    mock_shallow_clone,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "opensre" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "stale.txt").write_text("x", encoding="utf-8")
    mock_prepare.return_value = workspace
    mock_shallow_clone.side_effect = WorkspaceError("git clone failed")

    with (
        patch(
            "tools.architecture_issue_tool.repo_workspace.architecture_workspace_dir",
            return_value=workspace,
        ),
        pytest.raises(WorkspaceError, match="git clone failed"),
    ):
        clone_github_repo("Tracer-Cloud", "opensre", ref="main")

    assert not workspace.exists()


def test_shallow_clone_sha_fetches_commit_directly(tmp_path: Path) -> None:
    """Non-HEAD SHAs must use init+fetch, not clone --depth 1 + checkout."""
    from subprocess import CompletedProcess

    from tools.architecture_issue_tool.repo_workspace import _shallow_clone

    destination = tmp_path / "workspace"
    sha = "abcdef0123456789abcdef0123456789abcdef01"
    calls: list[tuple[str, ...]] = []

    def _fake_run_git(cwd: Path, *args: str, **_kwargs: object) -> CompletedProcess[str]:
        calls.append(args)
        return CompletedProcess(args=("git", *args), returncode=0, stdout="", stderr="")

    with patch(
        "tools.architecture_issue_tool.repo_workspace._run_git",
        side_effect=_fake_run_git,
    ):
        _shallow_clone(
            remote_url="https://github.com/org/repo.git",
            destination=destination,
            ref=sha,
            token=None,
        )

    assert calls[0] == ("init",)
    assert calls[1] == ("remote", "add", "origin", "https://github.com/org/repo.git")
    assert calls[2] == ("fetch", "--depth", "1", "origin", sha)
    assert calls[3] == ("checkout", "--detach", "FETCH_HEAD")
    assert not any(args[:1] == ("clone",) for args in calls)
