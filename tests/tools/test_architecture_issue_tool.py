"""Tests for find_architecture_violations tool wrapper."""

from __future__ import annotations

from pathlib import Path

from tests.tools.conftest import BaseToolContract
from tools import registry as registry_module
from tools.architecture_issue_tool.tool import find_architecture_violations


class TestFindArchitectureViolationsContract(BaseToolContract):
    def get_tool_under_test(self):
        return find_architecture_violations.__opensre_registered_tool__


def test_find_architecture_violations_scans_local_path_fixture(tmp_path: Path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "big.py").write_text("x = 1\n" * 501, encoding="utf-8")

    result = find_architecture_violations(
        owner="Tracer-Cloud",
        repo="opensre",
        local_path=str(tmp_path),
        categories=["oversized_file"],
    )

    assert result["available"] is True
    assert result["owner"] == "Tracer-Cloud"
    assert result["repo"] == "opensre"
    assert result["scan_summary"]["categories_scanned"] == ["oversized_file"]
    assert result["scan_summary"]["severity_counts"] == {"p0": 0, "p1": 0, "p2": 1}
    assert result["scan_summary"]["kind_counts"] == {"oversized_file": 1}
    assert result["scan_summary"]["coverage_complete"] is True
    assert result["scan_summary"]["categories_skipped"] == []
    assert len(result["violations"]) == 1
    assert len(result["refactor_tasks"]) == 1
    assert result["side_effects"] == []


def test_metadata_is_github_read_only() -> None:
    rt = find_architecture_violations.__opensre_registered_tool__
    assert rt.source == "github"
    assert rt.side_effect_level == "read_only"
    assert "owner" in rt.input_schema["properties"]
    assert "repo" in rt.input_schema["properties"]


def test_skill_guidance_is_attached_via_registry() -> None:
    registry_module.clear_tool_registry_cache()
    marker = "Required reply template"
    tools_by_name = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")
    }
    tool_def = tools_by_name["find_architecture_violations"]
    assert marker in tool_def.description
    assert marker in tool_def.skill_guidance
    assert "AUDIT_REPORT.md" in tool_def.skill_guidance
    assert "Hotspots and statistics" in tool_def.skill_guidance
    assert "scan_summary.hotspots" in tool_def.skill_guidance
    assert "find_architecture_violations" in tool_def.skill_guidance
    assert "prepare_architecture_workspace" not in tools_by_name
    assert "release_architecture_workspace" not in tools_by_name
