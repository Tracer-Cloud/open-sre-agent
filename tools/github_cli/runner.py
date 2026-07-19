"""Run authenticated ``gh`` subprocesses for the github_cli tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from tools.github_cli.credentials import resolve_github_token

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 120

# Top-level ``gh`` commands that must never run under OpenSRE-injected credentials.
# - auth: ``gh auth token`` prints GH_TOKEN to stdout (self-exfiltration)
# - extension: install/run can download and execute arbitrary code
# - workflow / run: trigger or re-run CI (arbitrary code via workflow YAML)
# - secret: mutate repository secrets
# - codespace / ssh-key / gpg-key / config: credential and host-config mutation surface
_DENIED_TOP_LEVEL_COMMANDS = frozenset(
    {
        "auth",
        "extension",
        "workflow",
        "run",
        "secret",
        "codespace",
        "ssh-key",
        "gpg-key",
        "config",
    }
)

# Global flags that consume a following value (after the ``gh`` binary).
# Note: ``-h`` is ``--help`` (boolean), not a short form of ``--hostname``.
_VALUE_FLAGS = frozenset(
    {
        "-R",
        "--repo",
        "--hostname",
        "--jq",
        "-t",
        "--template",
    }
)


def positional_gh_tokens(args: list[str] | tuple[str, ...]) -> list[str]:
    """Return command positionals, skipping leading global flags."""
    positionals: list[str] = []
    i = 0
    cleaned = [str(a) for a in args]
    while i < len(cleaned):
        token = cleaned[i]
        if not token or token == "--":
            i += 1
            continue
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if inline:
                i += 1
                continue
            if name in _VALUE_FLAGS and i + 1 < len(cleaned):
                nxt = cleaned[i + 1]
                if nxt and not nxt.startswith("-"):
                    i += 2
                    continue
            i += 1
            continue
        positionals.append(token)
        i += 1
        while i < len(cleaned):
            positionals.append(cleaned[i])
            i += 1
        break
    return positionals


def denied_gh_command(args: list[str] | tuple[str, ...]) -> str | None:
    """Return the blocked top-level ``gh`` command, or None if allowed."""
    positionals = positional_gh_tokens(args)
    if not positionals:
        return None
    command = positionals[0].lower()
    return command if command in _DENIED_TOP_LEVEL_COMMANDS else None


def build_gh_argv(*, args: list[str], repo: str | None = None) -> list[str]:
    """Build full argv for ``gh`` including optional ``-R owner/name``."""
    argv = ["gh"]
    cleaned_repo = (repo or "").strip()
    positionals = positional_gh_tokens(args)
    command = positionals[0].lower() if positionals else None
    if cleaned_repo and command != "api":
        argv.extend(["-R", cleaned_repo])
    argv.extend(str(a) for a in args)
    return argv


def _redact_secret(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "***")


def run_gh(
    *,
    args: list[str],
    repo: str | None = None,
    github_token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Execute ``gh`` with OpenSRE-resolved credentials.

    Never returns the token: denied subcommands that could print or misuse it are
    rejected before spawn, and any accidental token echo in stdout/stderr is
    redacted from the returned payload.
    """
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

    blocked = denied_gh_command(args)
    if blocked is not None:
        return {
            "ok": False,
            "error": (
                f"`gh {blocked}` is blocked by OpenSRE (credential / host-config / "
                "extension commands are not allowed via github_cli)."
            ),
            "error_type": "policy_error",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

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

    stdout = _redact_secret(completed.stdout or "", token)
    stderr = _redact_secret(completed.stderr or "", token)
    ok = completed.returncode == 0
    payload: dict[str, Any] = {
        "ok": ok,
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if not ok:
        error_text = stderr.strip() or stdout.strip() or f"gh exited with {completed.returncode}"
        payload["error"] = _redact_secret(error_text, token)
        payload["error_type"] = "gh_error"
    return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "build_gh_argv",
    "denied_gh_command",
    "positional_gh_tokens",
    "run_gh",
]
