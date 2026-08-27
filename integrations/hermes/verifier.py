"""Verifier for the local Hermes log integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("hermes")
def verify_hermes(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Verify that the configured Hermes log is a readable regular file."""
    raw_path = str(config.get("log_path") or "").strip()
    if not raw_path:
        return result("hermes", source, "missing", "Missing log_path.")

    log_path = Path(raw_path).expanduser()
    if not log_path.exists():
        return result("hermes", source, "failed", f"Hermes log file not found: {log_path}")
    if not log_path.is_file():
        return result("hermes", source, "failed", f"Hermes log path is not a file: {log_path}")

    try:
        with log_path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return result(
            "hermes",
            source,
            "failed",
            f"Hermes log file is not readable: {log_path} ({type(exc).__name__})",
        )

    return result("hermes", source, "passed", f"Readable Hermes log file: {log_path}")


__all__ = ["verify_hermes"]
