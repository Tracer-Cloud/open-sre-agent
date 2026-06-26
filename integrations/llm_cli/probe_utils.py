"""Shared subprocess probe helpers for CLI adapters."""

from __future__ import annotations

import subprocess


def run_version_probe(binary_path: str, *, timeout_sec: float) -> tuple[str | None, str | None]:
    """Run ``<binary> --version`` and return ``(combined_output, error_detail)``.

    ``error_detail`` is suitable for ``CLIProbe.detail`` when the version probe
    fails. On success, ``combined_output`` is ``stdout + stderr``.
    """
    try:
        proc = subprocess.run(
            [binary_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Could not run `{binary_path} --version`: {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, f"`{binary_path} --version` failed: {err or 'unknown error'}"

    return (proc.stdout or "") + (proc.stderr or ""), None
