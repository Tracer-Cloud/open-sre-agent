"""Clone GitHub repositories into the architecture audit workspace."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import PROJECT_ROOT
from integrations.git.errors import GIT_UNAVAILABLE, GitCommandError
from integrations.git.local import _token_auth_env

_GITHUB_HTTPS_BASE = "https://github.com/"
_GIT_CLONE_TIMEOUT_SEC = 120.0
_GIT_REMOTE_TIMEOUT_SEC = 15.0
_ARCHITECTURE_WORKSPACE_DIR = PROJECT_ROOT / ".temp" / "opensre" / "architecture_workspace"

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


def architecture_workspace_dir() -> Path:
    """Return the fixed local directory used for architecture audit git clones."""
    return _ARCHITECTURE_WORKSPACE_DIR


def architecture_sandbox_dir() -> Path:
    """Alias retained for callers; prefer :func:`architecture_workspace_dir`."""
    return architecture_workspace_dir()


def prepare_architecture_workspace() -> Path:
    """Reset and return the architecture audit clone directory."""
    workspace = architecture_workspace_dir()
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def cleanup_architecture_workspace(*, path: str | Path | None = None) -> Path:
    """Delete the architecture workspace. Refuses paths outside the fixed dir."""
    workspace = architecture_workspace_dir().resolve()
    target = workspace if path is None else Path(path).expanduser().resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise WorkspaceError(
            f"cleanup refused: path is outside architecture workspace ({workspace})"
        ) from exc
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    return target


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


def clone_github_repo(
    owner: str,
    repo: str,
    *,
    ref: str = "",
    token: str | None = None,
    local_path: str | None = None,
) -> RepoWorkspace:
    """Clone *owner*/*repo* into the architecture workspace (or use *local_path*).

    Unlike :func:`cloned_github_repo`, this does **not** delete the workspace on
    return — callers must invoke :func:`cleanup_architecture_workspace`.
    """
    normalized_owner = owner.strip()
    normalized_repo = repo.strip()
    if not normalized_owner or not normalized_repo:
        raise WorkspaceError("owner and repo are required.")

    if local_path:
        root = Path(local_path).expanduser().resolve()
        if not root.is_dir():
            raise WorkspaceError(f"local_path is not a directory: {root}")
        return RepoWorkspace(
            owner=normalized_owner,
            repo=normalized_repo,
            ref=ref.strip(),
            root=root,
        )

    destination = prepare_architecture_workspace()
    remote_url = github_remote_url(normalized_owner, normalized_repo)
    effective_ref = ref.strip() or _remote_default_branch(remote_url, token=token)

    try:
        _shallow_clone(
            remote_url=remote_url,
            destination=destination,
            ref=effective_ref,
            token=token,
        )
    except WorkspaceError:
        cleanup_architecture_workspace()
        raise
    except GitCommandError as exc:
        cleanup_architecture_workspace()
        if exc.kind == GIT_UNAVAILABLE:
            raise WorkspaceError(exc.message) from exc
        raise WorkspaceError(exc.message) from exc

    return RepoWorkspace(
        owner=normalized_owner,
        repo=normalized_repo,
        ref=effective_ref,
        root=destination,
    )


@contextmanager
def cloned_github_repo(
    owner: str,
    repo: str,
    *,
    ref: str = "",
    token: str | None = None,
    local_path: str | None = None,
) -> Iterator[RepoWorkspace]:
    """Yield a workspace, cleaning the fixed architecture workspace on exit.

    When *local_path* is provided (tests/dev only), the path is yielded as-is and
    never deleted. Otherwise a shallow clone is created under
    ``.temp/opensre/architecture_workspace`` and removed on exit.
    """
    workspace = clone_github_repo(
        owner,
        repo,
        ref=ref,
        token=token,
        local_path=local_path,
    )
    try:
        yield workspace
    finally:
        if local_path is None:
            cleanup_architecture_workspace()
