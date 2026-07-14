"""Load action-agent skill recipes from bundled markdown files.

Skills are plain-markdown recipes that teach the action planner how to map a
recognisable request shape onto a concrete sequence of tool calls. Each skill
lives in its own ``*.md`` file under ``skills/`` and is concatenated, in stable
filename order, into a single ``SKILLS`` section that the action-agent prompt
appends after ``_SYSTEM_PROMPT_BASE``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ("SKILLS_HEADER", "load_skills_block", "skills_dir")

SKILLS_HEADER = f"{'=' * 40} SKILLS {'=' * 40}"

_SKILLS_DIRNAME = "skills"


def skills_dir() -> Path:
    """Return the directory that holds the bundled skill markdown files."""
    return Path(__file__).parent / _SKILLS_DIRNAME


@lru_cache(maxsize=1)
def load_skills_block() -> str:
    """Return the assembled SKILLS prompt section, or ``""`` when none exist.

    Skill bodies are read in ascending filename order so the rendered prompt is
    deterministic. Empty files are skipped. When no skill files are present the
    function returns an empty string so callers can omit the block entirely.
    """
    directory = skills_dir()
    if not directory.is_dir():
        return ""

    bodies: list[str] = []
    for path in sorted(directory.glob("*.md")):
        body = path.read_text(encoding="utf-8").strip()
        if body:
            bodies.append(body)

    if not bodies:
        return ""

    return f"{SKILLS_HEADER}\n\n" + "\n\n".join(bodies) + "\n\n"
