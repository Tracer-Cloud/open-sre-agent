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


# ---------------------------------------------------------------------------
# XML attribute escaping in format_tool_skill_guidance
# ---------------------------------------------------------------------------


def test_format_tool_skill_guidance_escapes_double_quotes(tmp_path: Path) -> None:
    """description with a double-quote must be escaped to &quot; in the XML tag."""
    path = tmp_path / "SKILL.md"
    _write_skill(
        path,
        """
name: tricky-skill
description: 'Say "hello" clearly.'
tools:
  - some_tool
""".strip(),
        body="Follow the steps.",
    )

    result = load_tool_skill_guidance(path, known_tool_names=frozenset({"some_tool"}))
    assert result.skill is not None

    formatted = format_tool_skill_guidance(result.skill)
    # Raw quote must not appear inside the attribute
    assert 'description="Say "hello" clearly."' not in formatted
    # Escaped form must be present
    assert "&quot;" in formatted


def test_format_tool_skill_guidance_escapes_ampersand(tmp_path: Path) -> None:
    """& in the skill name must be escaped to &amp;."""
    path = tmp_path / "SKILL.md"
    _write_skill(
        path,
        """
name: my-skill
description: Handles A and B operations.
tools:
  - tool_a
""".strip(),
    )
    result = load_tool_skill_guidance(path, known_tool_names=frozenset({"tool_a"}))
    assert result.skill is not None

    # Patch the description to contain & (frontmatter validator blocks it; inject directly)
    skill_with_amp = result.skill.__class__(
        name=result.skill.name,
        description="A & B tool",
        content=result.skill.content,
        file_path=result.skill.file_path,
        tool_names=result.skill.tool_names,
    )
    formatted = format_tool_skill_guidance(skill_with_amp)
    assert "&amp;" in formatted
    assert 'description="A & B tool"' not in formatted
