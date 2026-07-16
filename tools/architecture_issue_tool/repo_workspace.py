"""Clone GitHub repositories into the architecture audit workspace."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import PROJECT_ROOT

_GITHUB_HTTPS_BASE = "https://github.com/"
_GIT_CLONE_TIMEOUT_SEC = 120.0
_GIT_REMOTE_TIMEOUT_SEC = 15.0
_ARCHITECTURE_WORKSPACE_DIR = PROJECT_ROOT / ".temp" / "opensre" / "architecture_workspace"

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


def _remove_tree(path: Path, *, action: str) -> None:
    """Delete *path* recursively; raise WorkspaceError on any failure.

    Unlike ``shutil.rmtree(..., ignore_errors=True)``, this never reports success
    when the tree is only partially removed (or not removed at all).
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise WorkspaceError(f"{action} failed: could not remove {path}: {exc}") from exc
    if path.exists():
        raise WorkspaceError(f"{action} failed: path still exists after removal ({path})")


def prepare_architecture_workspace() -> Path:
    """Reset and return the architecture audit clone directory."""
    workspace = architecture_workspace_dir()
    _remove_tree(workspace, action="prepare architecture workspace")
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
    _remove_tree(target, action="cleanup architecture workspace")
    return target


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


def _token_auth_env(token: str, base_url: str) -> dict[str, str]:
    """Inject an HTTPS Authorization header scoped to *base_url* via git config env.

    Kept local so this tools package does not import ``integrations`` (layer peers).
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(os.environ)
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = f"http.{base_url}.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = f"Authorization: Basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


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


def _git_ok(
    result: subprocess.CompletedProcess[str],
    *,
    fallback: str,
) -> None:
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip() or fallback
    raise WorkspaceError(detail)


def _shallow_clone_sha(
    *,
    remote_url: str,
    destination: Path,
    sha: str,
    token: str | None,
) -> None:
    """Fetch a single commit SHA without requiring it to be the remote HEAD.

    ``git clone --depth 1`` only materializes the tip of the default branch, so
    ``checkout <sha>`` fails for any non-HEAD commit. Init + ``fetch --depth 1
    origin <sha>`` asks the remote for that object directly.
    """
    env = _auth_env(token)
    destination.mkdir(parents=True, exist_ok=True)
    _git_ok(_run_git(destination, "init", env=env), fallback="git init failed")
    _git_ok(
        _run_git(destination, "remote", "add", "origin", remote_url, env=env),
        fallback="git remote add failed",
    )
    _git_ok(
        _run_git(destination, "fetch", "--depth", "1", "origin", sha, env=env),
        fallback=f"git fetch failed for SHA {sha}",
    )
    _git_ok(
        _run_git(destination, "checkout", "--detach", "FETCH_HEAD", env=env),
        fallback=f"git checkout failed for SHA {sha}",
    )


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
        _shallow_clone_sha(
            remote_url=remote_url,
            destination=destination,
            sha=ref.strip(),
            token=token,
        )
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
    _git_ok(result, fallback="git clone failed")


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
