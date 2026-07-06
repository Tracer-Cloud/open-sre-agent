"""Application-wide constants: prompts, limits, identifiers, and filesystem paths."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from config.constants.investigation import MAX_EXPANSIONS, MAX_INVESTIGATION_LOOPS
from config.constants.opensre import DEFAULT_RELEASE_VERSION
from config.constants.platform import IS_WINDOWS
from config.constants.posthog import (
    DEFAULT_POSTHOG_BOUNCE_THRESHOLD,
    DEFAULT_POSTHOG_BOUNCE_WINDOW,
    DEFAULT_POSTHOG_TIMEOUT_SECONDS,
    DEFAULT_POSTHOG_URL,
    POSTHOG_CAPTURE_API_KEY,
    POSTHOG_HOST,
)
from config.constants.sentry import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_IN_APP_INCLUDE,
    SENTRY_MAX_BREADCRUMBS,
    SENTRY_TRACES_SAMPLE_RATE,
)

OPENSRE_HOME_DIR: Path = Path.home() / ".opensre"
INTEGRATIONS_STORE_PATH: Path = OPENSRE_HOME_DIR / "integrations.json"
# Legacy read-fallback for migrating pre-OpenSRE installs from ~/.tracer.
LEGACY_INTEGRATIONS_STORE_PATH: Path = Path.home() / ".tracer" / "integrations.json"
OPENSRE_TMP_DIR: Path = Path(tempfile.gettempdir()) / "opensre"


def get_store_path() -> Path:
    """Default path to the wizard config file (``~/.opensre/opensre.json``).

    Lives in ``config.constants`` (rather than ``surfaces/cli/wizard/store.py``)
    so layers below ``surfaces/`` — notably ``platform/`` — can read the
    store path without importing from a surface. The wizard's
    ``surfaces.cli.wizard.store`` module re-exports this name for the
    callers that already import it from there.

    Honors ``OPENSRE_WIZARD_STORE_PATH`` when set, mirroring the
    ``OPENSRE_LLM_AUTH_METADATA_PATH`` override in
    ``config.llm_auth.records``, so tests can redirect this store without
    every call site remembering to monkeypatch this function individually.
    """
    override = os.getenv("OPENSRE_WIZARD_STORE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / "opensre.json"


def ensure_opensre_tmp_dir() -> Path:
    """Create the OpenSRE temp directory with owner-only permissions when possible."""
    OPENSRE_TMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        OPENSRE_TMP_DIR.chmod(0o700)
    return OPENSRE_TMP_DIR


__all__ = [
    "DEFAULT_RELEASE_VERSION",
    "MAX_EXPANSIONS",
    "MAX_INVESTIGATION_LOOPS",
    "DEFAULT_POSTHOG_BOUNCE_THRESHOLD",
    "DEFAULT_POSTHOG_BOUNCE_WINDOW",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "INTEGRATIONS_STORE_PATH",
    "IS_WINDOWS",
    "LEGACY_INTEGRATIONS_STORE_PATH",
    "ensure_opensre_tmp_dir",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
    "SENTRY_DSN",
    "SENTRY_ERROR_SAMPLE_RATE",
    "SENTRY_IN_APP_INCLUDE",
    "SENTRY_MAX_BREADCRUMBS",
    "SENTRY_TRACES_SAMPLE_RATE",
]
