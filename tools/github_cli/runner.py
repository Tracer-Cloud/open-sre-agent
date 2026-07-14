"""Run authenticated ``gh`` subprocesses for the github_cli tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from integrations.github.client import resolve_github_token

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 120


def _first_positional_command(args: list[str]) -> str | None:
    """Return the first non-flag token in ``args`` (the ``gh`` subcommand)."""
    i = 0
    while i < len(args):
        token = str(args[i]).strip()
        if not token or token == "--":
            i += 1
            continue
        if token.startswith("-"):
            # Skip global flags that take a value (e.g. ``-R owner/name``).
            name, _, inline = token.partition("=")
            if inline:
                i += 1
                continue
            if name in {"-R", "--repo", "-h", "--hostname"} and i + 1 < len(args):
                nxt = str(args[i + 1])
                if nxt and not nxt.startswith("-"):
                    i += 2
                    continue
            i += 1
            continue
        return token.lower()
    return None


def build_gh_argv(*, args: list[str], repo: str | None = None) -> list[str]:
    """Build full argv for ``gh`` including optional ``-R owner/name``.
    """
    argv = ["gh"]
    cleaned_repo = (repo or "").strip()
    command = _first_positional_command(args)
    if cleaned_repo and command != "api":
        argv.extend(["-R", cleaned_repo])
    argv.extend(str(a) for a in args)
    return argv


def run_gh(
    *,
    args: list[str],
    repo: str | None = None,
    github_token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Execute ``gh`` with OpenSRE-resolved credentials.

    Never returns the token. On success/failure returns a structured payload
    suitable for agent consumption.
    """
    token = resolve_github_token(github_token)
    if not token:
        return {
            "ok": False,
            "error": "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN.",
            "error_type": "configuration_error",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    if shutil.which("gh") is None:
        return {
            "ok": False,
            "error": "The GitHub CLI (`gh`) is not installed or not on PATH.",
            "error_type": "missing_binary",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    if not args:
        return {
            "ok": False,
            "error": "args must be a non-empty list of arguments after `gh`.",
            "error_type": "validation_error",
            "argv": ["gh"],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    timeout_seconds = DEFAULT_TIMEOUT_SECONDS if timeout is None else int(timeout)
    timeout_seconds = max(1, min(timeout_seconds, MAX_TIMEOUT_SECONDS))
    argv = build_gh_argv(args=args, repo=repo)
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    # Prefer token auth over ambient gh keyring login.
    env.pop("GH_ENTERPRISE_TOKEN", None)

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"gh timed out after {timeout_seconds}s",
            "error_type": "timeout",
            "argv": argv,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"failed to start gh: {exc}",
            "error_type": "spawn_error",
            "argv": argv,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    ok = completed.returncode == 0
    payload: dict[str, Any] = {
        "ok": ok,
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if not ok:
        payload["error"] = (
            stderr.strip() or stdout.strip() or f"gh exited with {completed.returncode}"
        )
        payload["error_type"] = "gh_error"
    return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "build_gh_argv",
    "run_gh",
]
