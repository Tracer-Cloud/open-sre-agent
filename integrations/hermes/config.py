"""Configuration for the local Hermes log integration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config.constants.hermes import HERMES_LOG_PATH_ENV
from config.strict_config import StrictConfigModel

_DEFAULT_LOG_RELATIVE = (".hermes", "logs", "errors.log")


class HermesLogConfig(StrictConfigModel):
    """Resolved path used by the Hermes log investigation tool."""

    log_path: str
    integration_id: str = ""


def default_hermes_log_path() -> Path:
    """Resolve the configured Hermes log path or its standard location."""
    configured = os.getenv(HERMES_LOG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home().joinpath(*_DEFAULT_LOG_RELATIVE)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[HermesLogConfig | None, str | None]:
    """Normalize a stored or environment-backed Hermes log configuration."""
    log_path = str(credentials.get("log_path") or "").strip()
    if not log_path:
        return None, None
    return HermesLogConfig(log_path=log_path, integration_id=record_id), "hermes"


__all__ = ["HermesLogConfig", "classify", "default_hermes_log_path"]
