"""Tests for declarative tool skill guidance loading."""

from __future__ import annotations

from pathlib import Path

from core.tool_framework.skill_guidance import (
    format_tool_skill_guidance,
    load_tool_skill_guidance,
)


def _write_skill(path: Path, frontmatter: str, body: str = "Use this workflow.") -> None:
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_load_tool_skill_guidance_loads_valid_skill(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(
        path,
        """
name: github-workflow
description: Guide GitHub workflow tools.
tools:
  - list_github_work_items
  - generate_work_status_report
""".strip(),
        body="Read first, then report.",
    )

    result = load_tool_skill_guidance(
        path,
        known_tool_names=frozenset({"list_github_work_items", "generate_work_status_report"}),
    )

    assert result.diagnostics == []
    assert result.skill is not None
    assert result.skill.name == "github-workflow"
    assert result.skill.tool_names == ("list_github_work_items", "generate_work_status_report")
    assert "Read first" in result.skill.content

    formatted = format_tool_skill_guidance(result.skill)
    assert '<skill name="github-workflow"' in formatted
    assert 'description="Guide GitHub workflow tools."' in formatted
    assert f"References are relative to {tmp_path}" in formatted


def test_load_tool_skill_guidance_skips_missing_file(tmp_path: Path) -> None:
    result = load_tool_skill_guidance(tmp_path / "missing" / "SKILL.md")

    assert result.skill is None
    assert result.diagnostics == []


def test_load_tool_skill_guidance_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: [unterminated\n---\nBody\n", encoding="utf-8")

    result = load_tool_skill_guidance(path)

    assert result.skill is None
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["parse_failed"]


def test_load_tool_skill_guidance_warns_on_invalid_name_and_unknown_tool(
    tmp_path: Path,
) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(
        path,
        """
name: GitHub Workflow
description: Guide GitHub workflow tools.
tools:
  - list_github_work_items
  - missing_tool
""".strip(),
    )

    result = load_tool_skill_guidance(
        path,
        known_tool_names=frozenset({"list_github_work_items"}),
    )

    assert result.skill is not None
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "invalid_metadata",
        "unknown_tool",
    }


def test_load_tool_skill_guidance_requires_description_and_tools(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    _write_skill(path, "name: github-workflow")

    result = load_tool_skill_guidance(path)

    assert result.skill is None
    messages = [diagnostic.message for diagnostic in result.diagnostics]
    assert "description is required" in messages
    assert "tools must be a non-empty list of names" in messages
