"""Clone GitHub repositories into ephemeral workspaces for architecture scans."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from integrations.git.errors import GIT_UNAVAILABLE, GitCommandError
from integrations.git.local import _token_auth_env

_GITHUB_HTTPS_BASE = "https://github.com/"
_GIT_CLONE_TIMEOUT_SEC = 120.0
_GIT_REMOTE_TIMEOUT_SEC = 15.0
_WORKSPACE_PREFIX = "opensre-arch-audit-"

_SKIP_SCAN_ROOT_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "docs",
        "node_modules",
        "opensre.egg-info",
        "packaging",
        "tests",
        "venv",
    }
)

_SHA_REF_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


class WorkspaceError(Exception):
    """Failed to prepare a repository workspace for scanning."""


@dataclass(frozen=True)
class RepoWorkspace:
    """Resolved local workspace for a GitHub repository audit."""

    owner: str
    repo: str
    ref: str
    root: Path


def github_remote_url(owner: str, repo: str) -> str:
    """Return the HTTPS git remote URL for a GitHub repository."""
    return f"{_GITHUB_HTTPS_BASE}{owner.strip()}/{repo.strip()}.git"


def resolve_scan_roots(clone_root: Path) -> list[Path]:
    """Return top-level package directories to scan under *clone_root*."""
    roots: list[Path] = []
    if not clone_root.is_dir():
        return roots

    for child in sorted(clone_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in _SKIP_SCAN_ROOT_DIRS:
            continue
        if not any(child.rglob("*.py")):
            continue
        roots.append(child)
    return roots


def _run_git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = _GIT_CLONE_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git command timed out after {timeout:.0f}s.") from exc


def _auth_env(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    return _token_auth_env(token, _GITHUB_HTTPS_BASE)


def _remote_default_branch(remote_url: str, *, token: str | None) -> str:
    env = _auth_env(token)
    with tempfile.TemporaryDirectory(prefix="opensre-arch-remote-") as tmp:
        result = _run_git(
            Path(tmp),
            "ls-remote",
            "--symref",
            remote_url,
            "HEAD",
            env=env,
            timeout=_GIT_REMOTE_TIMEOUT_SEC,
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "ls-remote failed"
        raise WorkspaceError(f"Could not resolve default branch: {detail}")

    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].removeprefix("refs/heads/")
    raise WorkspaceError("Could not resolve default branch from ls-remote output.")


def _looks_like_sha(ref: str) -> bool:
    return bool(_SHA_REF_RE.fullmatch(ref.strip()))


def _shallow_clone(
    *,
    remote_url: str,
    destination: Path,
    ref: str,
    token: str | None,
) -> None:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    env = _auth_env(token)

    if _looks_like_sha(ref):
        result = _run_git(parent, "clone", "--depth", "1", remote_url, str(destination), env=env)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "git clone failed"
            raise WorkspaceError(detail)
        checkout = _run_git(destination, "checkout", ref, env=env)
        if checkout.returncode != 0:
            detail = checkout.stderr.strip() or checkout.stdout.strip() or "git checkout failed"
            raise WorkspaceError(detail)
        return

    result = _run_git(
        parent,
        "clone",
        "--depth",
        "1",
        "--branch",
        ref,
        remote_url,
        str(destination),
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git clone failed"
        raise WorkspaceError(detail)


@contextmanager
def cloned_github_repo(
    owner: str,
    repo: str,
    *,
    ref: str = "",
    token: str | None = None,
    local_path: str | None = None,
) -> Iterator[RepoWorkspace]:
    """Yield a workspace for *owner*/*repo*, cloning when *local_path* is unset.

    When *local_path* is provided (tests/dev only), the path is yielded as-is and
    never deleted. Otherwise a shallow clone is created under a temp directory and
    removed on exit, including when the caller raises.
    """
    normalized_owner = owner.strip()
    normalized_repo = repo.strip()
    if not normalized_owner or not normalized_repo:
        raise WorkspaceError("owner and repo are required.")

    if local_path:
        root = Path(local_path).expanduser().resolve()
        if not root.is_dir():
            raise WorkspaceError(f"local_path is not a directory: {root}")
        yield RepoWorkspace(
            owner=normalized_owner,
            repo=normalized_repo,
            ref=ref.strip(),
            root=root,
        )
        return

    parent = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX))
    destination = parent / "repo"
    remote_url = github_remote_url(normalized_owner, normalized_repo)
    effective_ref = ref.strip() or _remote_default_branch(remote_url, token=token)

    try:
        _shallow_clone(
            remote_url=remote_url,
            destination=destination,
            ref=effective_ref,
            token=token,
        )
        yield RepoWorkspace(
            owner=normalized_owner,
            repo=normalized_repo,
            ref=effective_ref,
            root=destination,
        )
    except WorkspaceError:
        raise
    except GitCommandError as exc:
        if exc.kind == GIT_UNAVAILABLE:
            raise WorkspaceError(exc.message) from exc
        raise WorkspaceError(exc.message) from exc
    finally:
        if parent.exists():
            shutil.rmtree(parent, ignore_errors=True)
