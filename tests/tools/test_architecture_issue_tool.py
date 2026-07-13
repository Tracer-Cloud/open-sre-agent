"""Tests for architecture action tools (clone and cleanup)."""

from __future__ import annotations

from pathlib import Path

from tests.tools.conftest import BaseToolContract
from tools import registry as registry_module
from tools.architecture_issue_tool.tool import (
    architecture_cleanup_repo,
    architecture_clone_repo,
)


class TestArchitectureCloneRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_clone_repo.__opensre_registered_tool__


class TestArchitectureCleanupRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_cleanup_repo.__opensre_registered_tool__


def test_architecture_tools_are_action_surface_only() -> None:
    registry_module.clear_tool_registry_cache()
    action = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("action")
    }
    chat = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")}

    for name in (
        "architecture_clone_repo",
        "architecture_cleanup_repo",
    ):
        assert name in action
        assert name not in chat

    assert "scan_architecture_imports" not in action
    assert "scan_module_placement" not in action
    assert "find_architecture_violations" not in action
    assert "find_architecture_violations" not in chat


def test_architecture_clone_repo_local_path(tmp_path: Path) -> None:
    result = architecture_clone_repo(
        owner="org",
        repo="repo",
        local_path=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["workspace_root"] == str(tmp_path.resolve())


def test_architecture_cleanup_refuses_outside_path(tmp_path: Path) -> None:
    result = architecture_cleanup_repo(workspace_root=str(tmp_path))
    assert result["ok"] is False
    assert "outside" in result["error"]
