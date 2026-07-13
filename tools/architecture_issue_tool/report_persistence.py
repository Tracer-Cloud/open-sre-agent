"""Persist full architecture-audit observation lists under ``~/.opensre``."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

_SAFE_REPO_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ReportPersistenceError(Exception):
    """Failed to persist an architecture audit observations file."""


def sanitize_repo_name(repo_name: str) -> str:
    """Return a filesystem-safe repo slug for observation filenames."""
    cleaned = _SAFE_REPO_RE.sub("-", (repo_name or "").strip()).strip(".-_")
    return cleaned or "repo"


def architecture_observations_dir(session_id: str, *, home_dir: Path | None = None) -> Path:
    """Return ``~/.opensre/{session_id}/`` for architecture audit artifacts."""
    root = home_dir if home_dir is not None else OPENSRE_HOME_DIR
    sid = (session_id or "").strip()
    if not sid:
        raise ReportPersistenceError("session_id is required to save architecture observations")
    if "/" in sid or "\\" in sid or sid in {".", ".."}:
        raise ReportPersistenceError("session_id must be a single path segment")
    return root / sid


def architecture_observations_path(
    session_id: str,
    repo_name: str,
    *,
    audit_id: str | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Build ``~/.opensre/{session_id}/{repo_name}-architecture-audit-{uuid}.md``."""
    rid = (audit_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    filename = f"{sanitize_repo_name(repo_name)}-architecture-audit-{rid}.md"
    return architecture_observations_dir(session_id, home_dir=home_dir) / filename


def render_observations_markdown(
    *,
    session_id: str,
    repo_name: str,
    observations: str,
    audit_id: str,
    saved_at: str | None = None,
) -> str:
    """Render the on-disk markdown document for a full observations list."""
    body = (observations or "").strip()
    if not body:
        raise ReportPersistenceError("observations content is empty")
    stamp = saved_at or datetime.now(UTC).isoformat()
    return (
        "# Architecture audit observations\n\n"
        f"- **session_id:** {session_id}\n"
        f"- **repo:** {repo_name.strip() or sanitize_repo_name(repo_name)}\n"
        f"- **audit_id:** {audit_id}\n"
        f"- **saved_at:** {stamp}\n\n"
        "## Full observation list\n\n"
        f"{body}\n"
    )


def save_architecture_observations(
    *,
    session_id: str,
    repo_name: str,
    observations: str,
    audit_id: str | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Write the full observations list and return the saved path."""
    rid = (audit_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    path = architecture_observations_path(
        session_id,
        repo_name,
        audit_id=rid,
        home_dir=home_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_observations_markdown(
        session_id=session_id.strip(),
        repo_name=repo_name,
        observations=observations,
        audit_id=rid,
    )
    path.write_text(content, encoding="utf-8")
    return path


__all__ = [
    "ReportPersistenceError",
    "architecture_observations_dir",
    "architecture_observations_path",
    "render_observations_markdown",
    "sanitize_repo_name",
    "save_architecture_observations",
]
