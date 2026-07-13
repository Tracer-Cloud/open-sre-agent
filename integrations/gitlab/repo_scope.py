"""Infer GitLab project and file scope for repository-scoped tool calls."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

GitlabRepoScope = tuple[str, str, str]

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_SSH_REMOTE_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<path>.+)$", re.IGNORECASE)
_PROJECT_QUALIFIER_RE = re.compile(
    r"\bgitlab:(?P<project>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)",
    re.IGNORECASE,
)


def _clean_project_path(value: str) -> str:
    return value.strip().strip("/").removesuffix(".git")


def _scope_from_url(value: str) -> GitlabRepoScope | None:
    parsed = urlsplit(value.rstrip(".,);]"))
    host = (parsed.hostname or "").lower()
    if "gitlab" not in host:
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    blob_marker = "/-/blob/"
    if blob_marker in path:
        project, remainder = path.split(blob_marker, 1)
        ref, separator, file_path = remainder.partition("/")
        project = _clean_project_path(project)
        if project and ref and separator and file_path:
            return project, ref, file_path
        return None

    project = _clean_project_path(path.split("/-/", 1)[0])
    return (project, "", "") if "/" in project else None


def parse_gitlab_repository_reference(text: str) -> GitlabRepoScope | None:
    """Return the last GitLab project/file scope found in *text*."""
    if not text.strip():
        return None

    matches: list[GitlabRepoScope] = []
    for match in _URL_RE.finditer(text):
        scope = _scope_from_url(match.group(0))
        if scope:
            matches.append(scope)
    for match in _PROJECT_QUALIFIER_RE.finditer(text):
        project = _clean_project_path(match.group("project"))
        if project:
            matches.append((project, "", ""))
    return matches[-1] if matches else None


def _parse_git_remote_scope(url: str) -> GitlabRepoScope | None:
    cleaned = url.strip()
    ssh_match = _SSH_REMOTE_RE.match(cleaned)
    if ssh_match:
        if "gitlab" not in ssh_match.group("host").lower():
            return None
        project = _clean_project_path(ssh_match.group("path"))
        return (project, "", "") if "/" in project else None
    return _scope_from_url(cleaned)


def detect_git_remote_repo_scope(cwd: str | Path | None = None) -> GitlabRepoScope | None:
    """Best-effort GitLab project scope from ``git remote get-url origin``."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=Path(cwd or os.getcwd()),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_git_remote_scope(result.stdout)


def infer_gitlab_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: GitlabRepoScope | None = None,
) -> GitlabRepoScope | None:
    """Resolve GitLab scope from message, history, cache, environment, or git."""
    from_message = parse_gitlab_repository_reference(message)
    if from_message:
        return from_message

    if conversation_messages:
        for _role, content in reversed(conversation_messages):
            from_history = parse_gitlab_repository_reference(content)
            if from_history:
                return from_history

    if cached:
        return cached

    env_map = env if env is not None else os.environ
    project = _clean_project_path(str(env_map.get("GITLAB_PROJECT_ID", "")))
    if project:
        return (
            project,
            str(env_map.get("GITLAB_REF", "")).strip(),
            str(env_map.get("GITLAB_FILE_PATH", "")).strip().strip("/"),
        )

    return detect_git_remote_repo_scope(cwd)


def apply_gitlab_repo_scope(
    resolved: dict[str, Any],
    project_id: str,
    ref_name: str,
    file_path: str,
) -> dict[str, Any]:
    """Return a copy of *resolved* enriched with GitLab repository scope."""
    gitlab = resolved.get("gitlab")
    if not gitlab:
        return dict(resolved)

    if isinstance(gitlab, BaseModel):
        gitlab_dict = gitlab.model_dump(exclude_none=True)
    elif isinstance(gitlab, dict):
        gitlab_dict = dict(gitlab)
    else:
        return dict(resolved)

    gitlab_dict["project_id"] = project_id
    if ref_name:
        gitlab_dict["ref_name"] = ref_name
    if file_path:
        gitlab_dict["file_path"] = file_path

    merged = dict(resolved)
    merged["gitlab"] = gitlab_dict
    return merged


__all__ = [
    "GitlabRepoScope",
    "apply_gitlab_repo_scope",
    "detect_git_remote_repo_scope",
    "infer_gitlab_repo_scope",
    "parse_gitlab_repository_reference",
]
