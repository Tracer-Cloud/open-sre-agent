"""Tests for ArchitectureIssueTool."""

from __future__ import annotations

from pathlib import Path

from tests.tools.conftest import BaseToolContract
from tools.architecture_issue_tool import find_architecture_violations


class TestArchitectureIssueToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return find_architecture_violations.__opensre_registered_tool__


def test_find_architecture_violations_mock_project(tmp_path: Path) -> None:
    # 1. Create a dummy packages structure
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    integrations_dir = tmp_path / "integrations"
    integrations_dir.mkdir()
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()

    # 2. Add an import violation: core importing integrations
    core_file = core_dir / "module.py"
    core_file.write_text("from integrations.some_integration import helper\n", encoding="utf-8")

    # 3. Add an oversized file
    big_file = core_dir / "big.py"
    big_file.write_text("\n".join(["line"] * 10) + "\n", encoding="utf-8")

    # 4. Add a compatibility shim
    shim_file = integrations_dir / "shim.py"
    shim_content = '"""Forwarding shim."""\nfrom core.module import foo\n__all__ = ["foo"]\n'
    shim_file.write_text(shim_content, encoding="utf-8")

    # 5. Add a misplaced tool module
    misplaced_file = core_dir / "misplaced_tool.py"
    misplaced_content = (
        "from tools.tool_decorator import tool\n"
        '@tool(name="bad_tool", source="knowledge")\n'
        "def run_tool():\n"
        "    pass\n"
    )
    misplaced_file.write_text(misplaced_content, encoding="utf-8")

    # 6. Add misplaced client file in tools
    tools_client_file = tools_dir / "my_client.py"
    tools_client_file.write_text("class MyClient:\n    pass\n", encoding="utf-8")

    # Run tool
    result = find_architecture_violations(repo_root=str(tmp_path), max_file_lines=4)

    violations = result["violations"]
    proposed_tasks = result["proposed_refactor_tasks"]

    # Assertions
    # 1. Dependency direction check
    dep_violations = [v for v in violations if v["type"] == "dependency_direction"]
    assert len(dep_violations) > 0
    assert any("violating the 'core -> integrations'" in v["description"] for v in dep_violations)

    # 2. Oversized file check
    oversized_violations = [v for v in violations if v["type"] == "oversized_file"]
    assert len(oversized_violations) > 0
    assert any("big.py" in v["file_path"] for v in oversized_violations)

    # 3. Compatibility shim check
    shim_violations = [v for v in violations if v["type"] == "compatibility_shim"]
    assert len(shim_violations) > 0
    assert any("shim.py" in v["file_path"] for v in shim_violations)

    # 4. Misplaced module check
    misplaced_violations = [v for v in violations if v["type"] == "misplaced_module"]
    assert len(misplaced_violations) >= 2
    paths = [v["file_path"] for v in misplaced_violations]
    assert "core/misplaced_tool.py" in paths
    assert "tools/my_client.py" in paths

    # Proposed tasks checks
    assert len(proposed_tasks) > 0
    assert any(t["priority"] == "high" for t in proposed_tasks)


def test_find_architecture_violations_default_detection() -> None:
    # Run the tool on the current repository root to make sure it doesn't crash
    result = find_architecture_violations(max_file_lines=10000)
    assert "violations" in result
    assert "proposed_refactor_tasks" in result
    assert isinstance(result["violations"], list)
    assert isinstance(result["proposed_refactor_tasks"], list)
