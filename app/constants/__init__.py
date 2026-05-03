"""Application-wide constants: prompts, limits, identifiers, and filesystem paths."""

from __future__ import annotations

import os
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
OPENSRE_TMP_DIR: Path = (
    Path("/tmp/opensre") if os.name != "nt" else Path(tempfile.gettempdir()) / "opensre"
)

__all__ = [
    "DEFAULT_POSTHOG_BOUNCE_THRESHOLD",
    "DEFAULT_POSTHOG_BOUNCE_WINDOW",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "INTEGRATIONS_STORE_PATH",
    "LEGACY_INTEGRATIONS_STORE_PATH",
    "LEGACY_TRACER_HOME_DIR",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
]
