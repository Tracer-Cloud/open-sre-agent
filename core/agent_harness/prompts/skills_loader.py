"""Load action-agent skill recipes from bundled markdown files.

Skills are plain-markdown recipes that teach the action planner how to map a
recognisable request shape onto a concrete sequence of tool calls.

Layout (either form is supported):

- Package: ``skills/<name>/SKILL.md`` (preferred) or ``skills/<name>/<name>.md``,
  with an optional sibling ``<name>_report.md`` report template in the same
  directory.
- Flat: ``skills/<name>.md`` with optional ``skills/<name>_report.md``.

All skill bodies are concatenated, in stable path order, into a single
``SKILLS`` section that the action-agent prompt appends after
``_SYSTEM_PROMPT_BASE``. When a report template is present, it is appended so
the final user-facing reply shape stays deterministic.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ("SKILLS_HEADER", "load_skills_block", "skills_dir")

SKILLS_HEADER = f"{'=' * 40} SKILLS {'=' * 40}"

_SKILLS_DIRNAME = "skills"
_PACKAGE_SKILL_FILENAME = "SKILL.md"
_REPORT_TEMPLATE_SUFFIX = "_report.md"
_REPO_SKILLS_PREFIX = "core/agent_harness/prompts/skills"
_REPORT_TEMPLATE_HEADER = "REPORT TEMPLATE from `{repo_path}` (fill exactly; keep all headings):"


def skills_dir() -> Path:
    """Return the directory that holds the bundled skill markdown files."""
    return Path(__file__).parent / _SKILLS_DIRNAME


def _repo_relative_path(path: Path) -> str:
    """Return a stable repo-relative path for prompt references."""
    try:
        relative = path.relative_to(skills_dir())
    except ValueError:
        return path.name
    return f"{_REPO_SKILLS_PREFIX}/{relative.as_posix()}"


def _package_skill_path(package_dir: Path) -> Path | None:
    """Return the skill recipe path inside a package directory, if present."""
    for candidate in (
        package_dir / _PACKAGE_SKILL_FILENAME,
        package_dir / f"{package_dir.name}.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def _iter_skill_paths(directory: Path) -> list[Path]:
    """Return skill recipe paths in stable order (packages then flat files)."""
    paths: list[Path] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_file = _package_skill_path(child)
        if skill_file is not None:
            paths.append(skill_file)
    paths.extend(sorted(directory.glob("*.md")))
    return paths


def _report_template_path(skill_path: Path) -> Path:
    """Return the sibling report template path for a skill recipe."""
    package_name = skill_path.parent.name
    if skill_path.name == _PACKAGE_SKILL_FILENAME:
        return skill_path.parent / f"{package_name}{_REPORT_TEMPLATE_SUFFIX}"
    return skill_path.with_name(f"{skill_path.stem}{_REPORT_TEMPLATE_SUFFIX}")


def _skill_body_with_optional_template(skill_path: Path) -> str:
    body = skill_path.read_text(encoding="utf-8").strip()
    if not body:
        return ""

    template_path = _report_template_path(skill_path)
    if not template_path.is_file():
        return body

    template = template_path.read_text(encoding="utf-8").strip()
    if not template:
        return body

    header = _REPORT_TEMPLATE_HEADER.format(repo_path=_repo_relative_path(template_path))
    return f"{body}\n\n{header}\n\n{template}"


@lru_cache(maxsize=1)
def load_skills_block() -> str:
    """Return the assembled SKILLS prompt section, or ``""`` when none exist.

    Skill bodies are read in ascending path order so the rendered prompt is
    deterministic. Empty files are skipped. When no skill files are present the
    function returns an empty string so callers can omit the block entirely.
    Matching sibling ``{package_or_stem}_report.md`` files are appended after
    each skill.
    """
    directory = skills_dir()
    if not directory.is_dir():
        return ""

    bodies: list[str] = []
    for path in _iter_skill_paths(directory):
        body = _skill_body_with_optional_template(path)
        if body:
            bodies.append(body)

    if not bodies:
        return ""

    return f"{SKILLS_HEADER}\n\n" + "\n\n".join(bodies) + "\n\n"
