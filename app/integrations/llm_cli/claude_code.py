"""Anthropic Claude Code CLI adapter (``claude -p``, non-interactive / print mode).

Env vars
--------
CLAUDE_CODE_BIN   Optional explicit path to the ``claude`` binary.
                  Blank or non-runnable paths are ignored; PATH + fallbacks apply.
CLAUDE_CODE_MODEL Optional model override (e.g. ``claude-opus-4-7``).
                  Unset or empty → omit ``--model``; CLI default applies.

Auth
----
Claude Code authenticates via ``ANTHROPIC_API_KEY`` (env var) or OAuth credentials
stored in ``~/.claude/.credentials.json`` after ``claude login``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from app.integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from app.integrations.llm_cli.binary_resolver import (
    resolve_cli_binary,
)

_CLAUDE_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
# Claude Code's `--version` does config/cache init that can spike past Codex's 3s
# budget on cold starts or when another claude process holds shared state.
_PROBE_TIMEOUT_SEC = 8.0


def _parse_semver(text: str) -> str | None:
    m = _CLAUDE_VERSION_RE.search(text)
    return m.group(1) if m else None


def _probe_cli_auth(binary_path: str) -> tuple[bool | None, str]:
    """Check Claude Code auth via `claude auth status` (local, no API call).

    Covers both subscription login and ANTHROPIC_API_KEY; subscription takes
    priority as reported by the CLI itself.
    """
    try:
        proc = subprocess.run(
            [binary_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            f"claude auth status timed out after {_PROBE_TIMEOUT_SEC:.0f} s — auth state unknown.",
        )
    except OSError as exc:
        return None, f"Could not spawn claude for auth probe: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        return None, f"claude auth status failed: {err or 'unknown error'}"
    try:
        data = json.loads(proc.stdout)
        if not data.get("loggedIn"):
            return False, "Not authenticated. Run: claude auth login  or set ANTHROPIC_API_KEY."
        api_key_source = data.get("apiKeySource", "")
        if api_key_source:
            return True, f"Authenticated via {api_key_source}."
        email = data.get("email", "")
        return True, f"Authenticated via Claude subscription{f' ({email})' if email else ''}."
    except (json.JSONDecodeError, AttributeError):
        # Older CLI versions may not output JSON — treat exit 0 as authenticated.
        return True, "Authenticated via Claude CLI."


def _classify_claude_code_auth(binary_path: str | None = None) -> tuple[bool | None, str]:
    """Return (logged_in, detail) for Claude Code auth.

    Resolution order:
    1. Binary available → `claude auth status` is the source of truth for all
       platforms; covers both subscription login and ANTHROPIC_API_KEY.
    2. No binary, ANTHROPIC_API_KEY set → True (filesystem-independent fallback).
    3. No binary, credentials file present → True (OAuth login).
    4. No binary, macOS → None (Keychain may hold credentials; invocation will verify).
    5. No binary, Linux/Windows → False.
    """
    if binary_path:
        return _probe_cli_auth(binary_path)
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True, "Authenticated via ANTHROPIC_API_KEY."
    creds_path = Path.home() / ".claude" / ".credentials.json"
    try:
        if creds_path.exists() and creds_path.stat().st_size > 2:
            return True, "Authenticated via ~/.claude/.credentials.json (OAuth login)."
    except OSError:
        return None, "Could not read ~/.claude/.credentials.json; auth state unclear."
    if sys.platform == "darwin":
        return None, (
            "Auth state unclear — binary unavailable for verification. "
            "Run: claude auth login  or set ANTHROPIC_API_KEY."
        )
    return (
        False,
        "Not authenticated. Run: claude auth login  or set ANTHROPIC_API_KEY.",
    )


def _fallback_claude_code_paths() -> list[str]:
    return _default_cli_fallback_paths("claude")


class ClaudeCodeAdapter:
    """Non-interactive Claude Code CLI (``claude -p``, print mode, no TTY)."""

    name = "claude-code"
    binary_env_key = "CLAUDE_CODE_BIN"
    install_hint = "npm i -g @anthropic-ai/claude-code"
    auth_hint = "Run: claude login  or set ANTHROPIC_API_KEY"
    min_version: str | None = None
    default_exec_timeout_sec = 120.0

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="CLAUDE_CODE_BIN",
            binary_names=_candidate_binary_names("claude"),
            fallback_paths=_fallback_claude_code_paths,
        )

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        try:
            ver_proc = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"Could not run `{binary_path} --version`: {exc}",
            )

        if ver_proc.returncode != 0:
            err = (ver_proc.stderr or ver_proc.stdout or "").strip()
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"`{binary_path} --version` failed: {err or 'unknown error'}",
            )

        version = _parse_semver(ver_proc.stdout + ver_proc.stderr)
        logged_in, auth_detail = _classify_claude_code_auth(binary_path=binary_path)
        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=auth_detail,
        )

    def detect(self) -> CLIProbe:
        binary = self._resolve_binary()
        if not binary:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=(
                    "Claude Code CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint}  or set CLAUDE_CODE_BIN."
                ),
            )
        return self._probe_binary(binary)

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Claude Code CLI not found. {self.install_hint}"
                " or set CLAUDE_CODE_BIN to the full binary path."
            )

        cwd = workspace or os.getcwd()

        argv: list[str] = [
            binary,
            "-p",
            "--output-format",
            "text",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        # Forward Anthropic auth vars explicitly rather than relying on a blanket
        # prefix allowlist, so they don't leak into other CLI adapters (e.g. Codex).
        env: dict[str, str] = {"NO_COLOR": "1"}
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            val = os.environ.get(key, "").strip()
            if val:
                env[key] = val

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=cwd,
            env=env,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        del stderr, returncode
        return (stdout or "").strip()

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        bits = [f"claude -p exited with code {returncode}"]
        if err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])
        return ". ".join(bits)
