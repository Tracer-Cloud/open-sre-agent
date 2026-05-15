"""Disk-backed store for user-authored markdown runbooks.

A runbook is a Markdown file with YAML frontmatter that declares the alert
shapes it applies to.  Files live under ``~/.config/opensre/runbooks/`` and are
loaded lazily by the planner during an investigation.

Required frontmatter keys:
    triggers: list[str]   # lowercase keyword fragments matched against the alert

Optional frontmatter keys:
    service: str          # matched against pipeline_name / alert service
    category: str         # informational; surfaced in `runbook list`
    title: str            # falls back to slug when absent
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.constants import OPENSRE_HOME_DIR

RUNBOOK_DIR: Path = OPENSRE_HOME_DIR / "runbooks"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_SAFE_SLUG_RE = re.compile(r"^[\w-]+$")


class RunbookValidationError(ValueError):
    """Raised when a runbook file is missing required frontmatter."""


@dataclass(frozen=True)
class Runbook:
    """Parsed runbook ready for retrieval and prompt injection."""

    slug: str
    title: str
    service: str | None
    category: str | None
    triggers: tuple[str, ...]
    body: str
    path: Path
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize into a plain dict for the agent state envelope."""
        return {
            "slug": self.slug,
            "title": self.title,
            "service": self.service,
            "category": self.category,
            "triggers": list(self.triggers),
            "body": self.body,
            "path": str(self.path),
        }


def _runbook_dir() -> Path:
    """Return RUNBOOK_DIR, re-resolved against the current OPENSRE_HOME_DIR.

    Tests monkeypatch ``app.constants.OPENSRE_HOME_DIR`` to redirect the store
    onto a tmp_path. The module-level ``RUNBOOK_DIR`` constant is captured at
    import time, so we re-resolve through the constants module here to honor
    the monkeypatch.
    """
    from app import constants as _constants

    return _constants.OPENSRE_HOME_DIR / "runbooks"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_without_frontmatter)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, text[match.end() :]


def _coerce_triggers(value: Any) -> tuple[str, ...]:
    """Normalize a frontmatter ``triggers`` value into a tuple of lowercase strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip().lower(),) if value.strip() else ()
    if isinstance(value, list | tuple):
        return tuple(str(item).strip().lower() for item in value if str(item).strip())
    return ()


def _parse_runbook_file(path: Path) -> Runbook:
    """Read and parse a single runbook file.

    Raises:
        RunbookValidationError: when required frontmatter is missing.
    """
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    triggers = _coerce_triggers(frontmatter.get("triggers"))
    if not triggers:
        raise RunbookValidationError(
            f"{path.name}: frontmatter must include a non-empty 'triggers' list"
        )

    slug = path.stem
    title_value = frontmatter.get("title")
    title = (
        str(title_value).strip()
        if title_value and str(title_value).strip()
        else slug.replace("-", " ").replace("_", " ").title()
    )
    service_value = frontmatter.get("service")
    service = str(service_value).strip() or None if service_value else None
    category_value = frontmatter.get("category")
    category = str(category_value).strip() or None if category_value else None

    extra = {
        key: value
        for key, value in frontmatter.items()
        if key not in {"title", "service", "category", "triggers"}
    }

    return Runbook(
        slug=slug,
        title=title,
        service=service,
        category=category,
        triggers=triggers,
        body=body.strip(),
        path=path,
        extra=extra,
    )


def load_all() -> list[Runbook]:
    """Load every valid runbook under ``RUNBOOK_DIR``.

    Missing directory → ``[]``. Invalid files are skipped silently so a single
    bad runbook can never break the planner during an investigation.
    """
    directory = _runbook_dir()
    if not directory.exists():
        return []
    runbooks: list[Runbook] = []
    for path in sorted(directory.glob("*.md")):
        try:
            runbooks.append(_parse_runbook_file(path))
        except (RunbookValidationError, OSError, yaml.YAMLError):
            continue
    return runbooks


def save(source: Path) -> Runbook:
    """Validate ``source`` and copy it into the runbook store.

    Returns the parsed Runbook after copying. Slug = source.stem.
    """
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"runbook source not found: {source}")

    parsed = _parse_runbook_file(source)

    if not _SAFE_SLUG_RE.match(parsed.slug):
        raise RunbookValidationError(
            f"filename stem {parsed.slug!r} is not a valid slug"
            " — use only letters, digits, hyphens, and underscores"
        )

    directory = _runbook_dir()
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{parsed.slug}.md"
    shutil.copyfile(source, dest)

    return replace(parsed, path=dest)


def remove(slug: str) -> bool:
    """Delete ``RUNBOOK_DIR/<slug>.md``. Return True if the file existed."""
    if not _SAFE_SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r} — only letters, digits, hyphens, and underscores allowed"
        )
    target = _runbook_dir() / f"{slug}.md"
    if not target.exists():
        return False
    target.unlink()
    return True
