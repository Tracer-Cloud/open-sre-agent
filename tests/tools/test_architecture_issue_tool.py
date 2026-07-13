"""Tests for architecture action tools (clone, import, placement, cleanup)."""

from __future__ import annotations

from pathlib import Path

from tests.tools.conftest import BaseToolContract
from tools import registry as registry_module
from tools.architecture_issue_tool.tool import (
    architecture_cleanup_repo,
    architecture_clone_repo,
    scan_architecture_imports,
    scan_module_placement_tool,
)


class TestArchitectureCloneRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_clone_repo.__opensre_registered_tool__


class TestScanArchitectureImportsContract(BaseToolContract):
    def get_tool_under_test(self):
        return scan_architecture_imports.__opensre_registered_tool__


class TestScanModulePlacementContract(BaseToolContract):
    def get_tool_under_test(self):
        return scan_module_placement_tool.__opensre_registered_tool__


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
        "scan_architecture_imports",
        "scan_module_placement",
        "architecture_cleanup_repo",
    ):
        assert name in action
        assert name not in chat

    assert "find_architecture_violations" not in action
    assert "find_architecture_violations" not in chat


def test_scan_architecture_imports_on_local_path(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = scan_architecture_imports(
        workspace_root=str(tmp_path),
        owner="org",
        repo="repo",
    )

    assert result["available"] is True
    assert result["scan_summary"]["categories_scanned"] == ["layer_import", "direct_import"]
    assert result["workspace_root"] == str(tmp_path.resolve())


def test_scan_module_placement_on_local_fixture(tmp_path: Path) -> None:
    package = tmp_path / "tools" / "community_followup_tool"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# placeholder\n", encoding="utf-8")

    result = scan_module_placement_tool(
        workspace_root=str(tmp_path),
        owner="org",
        repo="repo",
    )

    assert result["available"] is True
    assert result["scan_summary"]["kind_counts"].get("misplaced_module", 0) >= 1


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
