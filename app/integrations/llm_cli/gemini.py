"""Google Gemini CLI adapter (`gemini` headless mode, non-interactive)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

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

_GEMINI_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
_PROBE_TIMEOUT_SEC = 3.0
_DEFAULT_EXEC_TIMEOUT_SEC = 300.0

logger = logging.getLogger(__name__)


def _parse_semver(text: str) -> str | None:
    m = _GEMINI_VERSION_RE.search(text)
    return m.group(1) if m else None


def _fallback_gemini_paths() -> list[str]:
    return _default_cli_fallback_paths("gemini")


def _truthy_env(key: str) -> bool:
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _gemini_exec_timeout_sec() -> float:
    raw = os.getenv("GEMINI_CLI_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_EXEC_TIMEOUT_SEC
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning(
            "Invalid GEMINI_CLI_TIMEOUT_SECONDS=%r; using default %.0fs",
            raw,
            _DEFAULT_EXEC_TIMEOUT_SEC,
        )
        return _DEFAULT_EXEC_TIMEOUT_SEC
    if parsed <= 0:
        logger.warning(
            "Invalid GEMINI_CLI_TIMEOUT_SECONDS=%r; using default %.0fs",
            raw,
            _DEFAULT_EXEC_TIMEOUT_SEC,
        )
        return _DEFAULT_EXEC_TIMEOUT_SEC
    return parsed


def _classify_gemini_auth() -> tuple[bool | None, str]:
    if os.getenv("GEMINI_API_KEY", "").strip():
        return True, "Gemini CLI installed; GEMINI_API_KEY is set for headless auth."
    if os.getenv("GOOGLE_API_KEY", "").strip():
        return True, "Gemini CLI installed; GOOGLE_API_KEY is set for headless auth."

    if _truthy_env("GOOGLE_GENAI_USE_VERTEXAI"):
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()
        credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if project and location and credentials and Path(credentials).expanduser().is_file():
            return True, "Gemini CLI installed; Vertex AI service account env is configured."
        if project and location:
            return (
                None,
                "Gemini CLI installed; Vertex AI env is present, but ADC auth was not probed.",
            )
        return (
            False,
            "Vertex AI auth is incomplete. Set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION.",
        )

    return (
        None,
        "Gemini CLI installed; auth status is not directly probeable. "
        "Headless mode will use cached login credentials or Gemini/Google env auth.",
    )


def _redact_sensitive_values(text: str) -> str:
    redacted = _GOOGLE_API_KEY_RE.sub("<redacted-google-api-key>", text)
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(key, "").strip()
        if len(value) >= 4:
            redacted = redacted.replace(value, f"<{key}>")
    return redacted


def _extract_json_response(payload: Any) -> str | None:
    if isinstance(payload, dict):
        response = payload.get("response")
        if isinstance(response, str):
            return response.strip()
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message.strip()
    return None


class GeminiAdapter:
    """Non-interactive Gemini CLI (`gemini` headless mode)."""

    name = "gemini"
    binary_env_key = "GEMINI_BIN"
    install_hint = "npm install -g @google/gemini-cli"
    auth_hint = "Run: gemini, or set GEMINI_API_KEY for headless use"
    min_version: str | None = None
    default_exec_timeout_sec = _DEFAULT_EXEC_TIMEOUT_SEC
    env_passthrough_keys: tuple[str, ...] = ()
    env_passthrough_prefixes: tuple[str, ...] = ("GEMINI_", "GOOGLE_")

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="GEMINI_BIN",
            binary_names=_candidate_binary_names("gemini"),
            fallback_paths=_fallback_gemini_paths,
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

        logged_in, auth_detail = _classify_gemini_auth()
        return CLIProbe(
            installed=True,
            version=_parse_semver(ver_proc.stdout + ver_proc.stderr),
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
                detail="Gemini CLI not found on PATH or known install locations.",
            )
        return self._probe_binary(binary)

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                "Gemini CLI not found. Install with `npm install -g @google/gemini-cli` "
                "or set GEMINI_BIN."
            )

        cwd = workspace or os.getcwd()
        argv: list[str] = [
            binary,
            "--output-format",
            "json",
            "--approval-mode",
            "plan",
            "--prompt",
            "",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=cwd,
            env=None,
            timeout_sec=_gemini_exec_timeout_sec(),
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        _ = stderr
        _ = returncode
        out = (stdout or "").strip()
        if not out:
            return ""
        try:
            response = _extract_json_response(json.loads(out))
        except json.JSONDecodeError:
            response = None
        return response if response is not None else out

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        combined = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
        safe_tail = _redact_sensitive_values(combined[:2000])
        lower = combined.lower()
        if any(
            marker in lower
            for marker in (
                "auth",
                "api key",
                "credential",
                "login",
                "oauth",
                "permission denied",
            )
        ):
            bits = [
                f"gemini exited with code {returncode}",
                "authentication may be missing or expired",
                self.auth_hint,
            ]
        else:
            bits = [f"gemini exited with code {returncode}"]
        if safe_tail:
            bits.append(safe_tail)
        return ". ".join(bits)
