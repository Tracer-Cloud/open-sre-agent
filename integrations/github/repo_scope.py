"""Infer GitHub repository scope (owner/repo) for repo-scoped tool calls."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from config.constants.runtime_metadata import WORKSPACE_REPO_ENV_KEYS
from integrations.github.identity import workspace_public_repository_source

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

_PUBLIC_WORKSPACE_SCOPE_MARKER = "public_workspace"


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
    source = workspace_public_repository_source({"workspace_repo": url})
    github = source.get("github", {})
    owner = str(github.get("owner") or "")
    repo = str(github.get("repo") or "")
    return (owner, repo) if owner and repo else None


def detect_git_remote_repo_scope(cwd: str | Path | None = None) -> tuple[str, str] | None:
    """Best-effort ``owner/repo`` from the checkout's origin or upstream remote."""
    work_dir = Path(cwd or os.getcwd())
    for remote in ("origin", "upstream"):
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0:
            scope = _parse_git_remote_url(result.stdout)
            if scope is not None:
                return scope
    return None


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


def _infer_workspace_repo_scope(
    *,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
) -> tuple[str, str] | None:
    """Resolve only the process workspace repository, not prompt-provided scope."""
    env_map = env if env is not None else os.environ
    for key in WORKSPACE_REPO_ENV_KEYS:
        source = workspace_public_repository_source({"workspace_repo": str(env_map.get(key, ""))})
        github = source.get("github", {})
        owner = str(github.get("owner") or "")
        repo = str(github.get("repo") or "")
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


class _GithubVcsRepoScopeProvider:
    """Adapts GitHub's owner/repo scope to :class:`infrastructure.harness_ports.VcsRepoScopeProvider`."""

    vendor = "github"

    def infer(
        self,
        *,
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
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
        workspace_scope = _infer_workspace_repo_scope(env=env, cwd=cwd)
        if workspace_scope is None:
            return None
        return (*workspace_scope, _PUBLIC_WORKSPACE_SCOPE_MARKER)

    def apply(self, resolved: dict[str, Any], scope: tuple[str, ...]) -> dict[str, Any]:
        owner, repo = scope[0], scope[1]
        if (
            len(scope) >= 3
            and scope[2] == _PUBLIC_WORKSPACE_SCOPE_MARKER
            and "github" not in resolved
        ):
            return {
                **resolved,
                **workspace_public_repository_source({"workspace_repo": f"{owner}/{repo}"}),
            }
        return apply_github_repo_scope(resolved, owner, repo)


GITHUB_VCS_REPO_SCOPE_PROVIDER = _GithubVcsRepoScopeProvider()


__all__ = [
    "GITHUB_VCS_REPO_SCOPE_PROVIDER",
    "apply_github_repo_scope",
    "detect_git_remote_repo_scope",
    "infer_github_repo_scope",
    "parse_github_repository_reference",
    "split_repo_full_name",
]
