"""Kimi Code CLI adapter (`kimi -p`, non-interactive)."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tomllib

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from app.integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from app.integrations.llm_cli.binary_resolver import resolve_cli_binary

_KIMI_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_PROBE_TIMEOUT_SEC = 3.0


def _ver_tuple(version: str) -> tuple[int, int, int]:
    """Convert a semver string into a comparable (major, minor, patch) tuple."""
    parts = [int(m) for m in re.findall(r"\d+", version)][:3]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _parse_semver(text: str) -> str | None:
    m = _KIMI_VERSION_RE.search(text)
    return m.group(1) if m else None


def _classify_kimi_login_status(
    returncode: int, stdout: str, stderr: str
) -> tuple[bool | None, str]:
    """Classify Kimi login status output, following the Codex pattern."""
    # Handle None values (defensive against subprocess edge cases)
    stdout = stdout or ""
    stderr = stderr or ""
    text = (stdout + "\n" + stderr).lower()

    # Negative phrases first to avoid substring false-positives
    if "not logged in" in text or "no credentials" in text or "unauthorized" in text:
        return False, "Not logged in. Run: kimi login"
    if returncode == 0 and ("logged in" in text or "authenticated" in text):
        return True, (stdout.strip() or stderr.strip() or "Authenticated.").splitlines()[0]
    if "expired" in text or ("invalid" in text and "token" in text):
        return False, "Session expired. Re-authenticate: kimi login"
    if "rate limit" in text or "quota" in text:
        return True, "Authenticated but rate-limited; try again later."
    if "network" in text or "unreachable" in text or "dns" in text or "connection refused" in text:
        return None, "Network error while checking auth; will retry at invocation."
    if returncode != 0:
        tail = (stderr or stdout).strip()[:200]
        return (
            None,
            f"Auth status unclear (exit {returncode}): {tail}"
            if tail
            else f"Auth status unclear (exit {returncode}).",
        )
    return None, "Auth status unknown."


def _check_kimi_auth_fallback() -> tuple[bool | None, str]:
    """Fallback auth check: KIMI_API_KEY env var, then config.toml."""
    # First try KIMI_API_KEY environment variable
    if os.environ.get("KIMI_API_KEY", "").strip():
        return True, "Authenticated via KIMI_API_KEY environment variable."

    # Then try config.toml
    share_dir = os.environ.get("KIMI_SHARE_DIR", "~/.kimi")
    config_path = pathlib.Path(os.path.expanduser(share_dir)) / "config.toml"

    if not config_path.exists():
        return False, "Not logged in. Run: kimi login"

    try:
        content = config_path.read_text(encoding="utf-8")
        config = tomllib.loads(content)
        providers = config.get("providers", {})
        if providers:
            for prov in providers.values():
                if str(prov.get("api_key", "")).strip():
                    return True, "Authenticated via config.toml."
        return False, "No API key configured. Run: kimi login"
    except Exception as e:
        return None, f"Could not verify auth status: {e}"


def _fallback_kimi_paths() -> list[str]:
    """Build a list of common install locations for Kimi (uv, cargo, pipx, etc.)."""
    # Kimi is installed via uv (Python tool) typically
    paths = _default_cli_fallback_paths("kimi")
    names = _candidate_binary_names("kimi")

    # Add pipx/uv standard paths if not already covered
    extra_dirs: list[str] = []
    if sys.platform == "win32":
        # Common locations for uv/cargo/pip on Windows
        extra_dirs.extend(
            [
                os.path.expandvars(r"%USERPROFILE%\.cargo\bin"),
                os.path.expandvars(r"%USERPROFILE%\.local\bin"),
                os.path.expandvars(r"%APPDATA%\uv\bin"),
            ]
        )
        # Search Python Scripts directories for recent versions
        for v in range(15, 11, -1):  # 3.15 down to 3.12
            extra_dirs.append(
                os.path.expandvars(rf"%USERPROFILE%\AppData\Roaming\Python\Python3{v}\Scripts")
            )
    else:
        # On Unix, ~/.local/bin is already in default_cli_fallback_paths.
        # Cargo might not be.
        extra_dirs.append(os.path.expanduser("~/.cargo/bin"))

    for d in extra_dirs:
        for name in names:
            paths.append(str(pathlib.Path(d) / name))

    return paths


class KimiAdapter:
    """Non-interactive Kimi Code CLI (`kimi -p` with --yolo)."""

    name = "kimi"
    binary_env_key = "KIMI_BIN"
    install_hint = "uv tool install --python 3.13 kimi-cli"
    auth_hint = "Run: kimi login"
    min_version: str | None = "1.40.0"
    default_exec_timeout_sec = 300.0

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="KIMI_BIN",
            binary_names=_candidate_binary_names("kimi"),
            fallback_paths=_fallback_kimi_paths,
        )

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        try:
            ver_proc = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
        upgrade_note = ""
        if self.min_version and version and _ver_tuple(version) < _ver_tuple(self.min_version):
            upgrade_note = (
                f" Kimi Code CLI {version} is below tested minimum {self.min_version}; "
                f"upgrade: uv tool upgrade kimi-cli"
            )

        # First, try 'kimi login status' for native CLI auth probe
        logged_in: bool | None = None
        auth_detail = ""
        try:
            auth_proc = subprocess.run(
                [binary_path, "login", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # login status unavailable; still fall back below (API key / config.toml).
            auth_detail = "Could not verify login status (timeout or OS error)."
        else:
            logged_in, auth_detail = _classify_kimi_login_status(
                auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
            )

        # `kimi login status` misses some API-key-only setups; timeouts return None —
        # both cases should merge env/config.toml auth when applicable.
        if logged_in is not True:
            logged_in_fb, auth_detail_fb = _check_kimi_auth_fallback()
            if logged_in is None or logged_in_fb is True:
                logged_in = logged_in_fb
                auth_detail = auth_detail_fb

        detail = auth_detail + upgrade_note
        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=detail.strip(),
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
                    "Kimi Code CLI not found. Install with "
                    "`uv tool install --python 3.13 kimi-cli`."
                ),
            )
        return self._probe_binary(binary)

    def build(
        self,
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> CLIInvocation:
        _ = reasoning_effort
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                "Kimi Code CLI not found. Install with "
                "`uv tool install --python 3.13 kimi-cli` or set KIMI_BIN."
            )

        ws = workspace or os.getcwd()

        # Every Kimi CLI invocation is forced into a one-shot, non-interactive mode.
        # We use --print and --yolo to ensure no interactive prompts block the agent.
        # Stdin is used via --input-format text to handle large prompts safely.
        argv: list[str] = [
            binary,
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "text",
            "--final-message-only",
            "--yolo",
            "-w",
            ws,
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=ws,
            env=None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        result = (stdout or "").strip()
        if not result:
            raise RuntimeError(
                self.explain_failure(stdout=stdout, stderr=stderr, returncode=returncode)
                + " (empty output)"
            )
        return result

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        bits = []
        if returncode != 0:
            bits.append(f"kimi exited with code {returncode}")

        if "LLM not set" in err or "LLM not set" in out:
            bits.append("Not logged in or model unavailable. Run: kimi login")
        elif "Error code: 401" in err or "Error code: 401" in out:
            bits.append("API key invalid or expired. Re-authenticate: kimi login")
        elif err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])

        if not bits and returncode == 0:
            return "kimi returned no output"

        return ". ".join(bits)
