"""Infer GitHub repository scope (owner/repo) for repo-scoped tool calls."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_GITHUB_HOST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)",
    re.IGNORECASE,
)
_REPO_QUALIFIER_RE = re.compile(
    r"\brepo:(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)",
    re.IGNORECASE,
)
_BARE_REPO_RE = re.compile(
    r"(?<![/\w.-])(?P<owner>[A-Za-z0-9][\w.-]*)/(?P<repo>[A-Za-z0-9][\w.-]*)(?![/\w.-])",
)


def split_repo_full_name(value: str) -> tuple[str, str]:
    """Split ``owner/repo`` (optionally with trailing ``.git``) into its parts."""
    cleaned = value.strip().strip("/")
    if cleaned.count("/") < 1:
        return "", ""
    owner, repo = cleaned.split("/", 1)
    return owner.strip(), repo.strip().removesuffix(".git")


def parse_github_repository_reference(text: str) -> tuple[str, str] | None:
    """Return the last owner/repo pair found in *text*, or ``None``."""
    if not text.strip():
        return None

    matches: list[tuple[str, str]] = []
    for pattern in (_GITHUB_HOST_RE, _REPO_QUALIFIER_RE, _BARE_REPO_RE):
        for match in pattern.finditer(text):
            owner = match.group("owner").strip()
            repo = match.group("repo").strip().removesuffix(".git")
            if owner and repo:
                matches.append((owner, repo))
    return matches[-1] if matches else None


def _parse_git_remote_url(url: str) -> tuple[str, str] | None:
    cleaned = url.strip()
    if not cleaned:
        return None
    if cleaned.startswith("git@"):
        _, _, path = cleaned.partition(":")
        if path:
            owner, repo = split_repo_full_name(path)
            return (owner, repo) if owner and repo else None
    return parse_github_repository_reference(cleaned)


def detect_git_remote_repo_scope(cwd: str | Path | None = None) -> tuple[str, str] | None:
    """Best-effort ``owner/repo`` from ``git remote get-url origin`` in *cwd*."""
    work_dir = Path(cwd or os.getcwd())
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_git_remote_url(result.stdout)


def infer_github_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    """Resolve GitHub owner/repo using message, history, session cache, env, and git."""
    from_message = parse_github_repository_reference(message)
    if from_message:
        return from_message

    if conversation_messages:
        for _role, content in reversed(conversation_messages):
            from_history = parse_github_repository_reference(content)
            if from_history:
                return from_history

    if cached:
        return cached

    env_map = env if env is not None else os.environ
    env_repo = str(env_map.get("GITHUB_REPOSITORY", "")).strip()
    if env_repo:
        owner, repo = split_repo_full_name(env_repo)
        if owner and repo:
            return owner, repo

    return detect_git_remote_repo_scope(cwd)


def apply_github_repo_scope(
    resolved: dict[str, Any],
    owner: str,
    repo: str,
) -> dict[str, Any]:
    """Return a copy of *resolved* with ``github.owner`` and ``github.repo`` set."""
    gh = resolved.get("github")
    if not gh:
        return dict(resolved)

    if isinstance(gh, BaseModel):
        gh_dict = gh.model_dump(exclude_none=True)
    elif isinstance(gh, dict):
        gh_dict = dict(gh)
    else:
        return dict(resolved)

    merged = dict(resolved)
    gh_dict["owner"] = owner
    gh_dict["repo"] = repo
    merged["github"] = gh_dict
    return merged


__all__ = [
    "apply_github_repo_scope",
    "detect_git_remote_repo_scope",
    "infer_github_repo_scope",
    "parse_github_repository_reference",
    "split_repo_full_name",
]
