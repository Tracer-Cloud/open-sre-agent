"""Application-wide constants: prompts, limits, identifiers, and filesystem paths."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from app.constants.posthog import (
    DEFAULT_POSTHOG_BOUNCE_THRESHOLD,
    DEFAULT_POSTHOG_BOUNCE_WINDOW,
    DEFAULT_POSTHOG_TIMEOUT_SECONDS,
    DEFAULT_POSTHOG_URL,
    POSTHOG_CAPTURE_API_KEY,
    POSTHOG_HOST,
)

OPENSRE_HOME_DIR: Path = Path.home() / ".opensre"
LEGACY_TRACER_HOME_DIR: Path = Path.home() / ".tracer"
INTEGRATIONS_STORE_PATH: Path = OPENSRE_HOME_DIR / "integrations.json"
LEGACY_INTEGRATIONS_STORE_PATH: Path = LEGACY_TRACER_HOME_DIR / "integrations.json"
OPENSRE_TMP_DIR: Path = Path(tempfile.gettempdir()) / "opensre"


def ensure_opensre_tmp_dir() -> Path:
    """Create the OpenSRE temp directory with owner-only permissions when possible."""
    OPENSRE_TMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        OPENSRE_TMP_DIR.chmod(0o700)
    return OPENSRE_TMP_DIR


__all__ = [
    "DEFAULT_POSTHOG_BOUNCE_THRESHOLD",
    "DEFAULT_POSTHOG_BOUNCE_WINDOW",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "INTEGRATIONS_STORE_PATH",
    "LEGACY_INTEGRATIONS_STORE_PATH",
    "LEGACY_TRACER_HOME_DIR",
    "ensure_opensre_tmp_dir",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
]
