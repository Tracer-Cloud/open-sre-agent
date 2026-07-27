"""Filesystem roots for OpenSRE data.

- :data:`OPENSRE_HOME_DIR` — host root (gateway pid/log, install catalog, analytics).
- :func:`opensre_home` — org context root when a Slack org principal is bound;
  otherwise the host root (laptop CLI / unbound).
- :func:`session_home` — one Slack member's conversation root under the org;
  otherwise the same as :func:`opensre_home`.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

from config.constants.memory import OPENSRE_MEMORY_DIR_ENV
from config.scope_context import current_scope

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT

SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "rds_postgres"

OPENSRE_HOME_DIR = Path.home() / ".opensre"
# Default unbound integrations path (docs / tests expecting the flat layout).
INTEGRATIONS_STORE_PATH = OPENSRE_HOME_DIR / "integrations.json"
OPENSRE_TMP_DIR = Path(tempfile.gettempdir()) / "opensre"

ORGS_DIR_NAME = "orgs"
MEMBERS_DIR_NAME = "members"

# Ids reach the filesystem; reject segments that could escape their directory.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class UnsafePathSegmentError(ValueError):
    """Raised when an id would escape its context directory."""


def _safe_segment(value: str, *, label: str) -> str:
    # "." and ".." match the character class but walk out of the directory.
    if not _SAFE_PATH_SEGMENT.match(value) or value.strip(".") == "":
        raise UnsafePathSegmentError(f"unsafe {label} for a context path: {value!r}")
    return value


def opensre_home() -> Path:
    """Context root for the bound org principal, or the host home when unbound.

    Slack turns with an org principal resolve to
    ``~/.opensre/orgs/<clerk_org_id>/``. CLI and other unbound callers keep
    ``~/.opensre/``.
    """
    scope = current_scope()
    if scope is None or scope.principal.kind != "org":
        return OPENSRE_HOME_DIR
    org_id = _safe_segment(scope.principal.id, label="principal id")
    return OPENSRE_HOME_DIR / ORGS_DIR_NAME / org_id


def session_home() -> Path:
    """One member's conversation root inside the org, or :func:`opensre_home`.

    With an org principal and Slack actor bound:
    ``~/.opensre/orgs/<org_id>/members/<slack_user_id>/``.
    """
    org_root = opensre_home()
    scope = current_scope()
    if scope is None or scope.principal.kind != "org":
        return org_root
    actor_id = _safe_segment(scope.actor.id, label="actor id")
    return org_root / MEMBERS_DIR_NAME / actor_id


def integrations_store_path() -> Path:
    """Integrations store path (shared by every member of the org)."""
    return opensre_home() / "integrations.json"


def get_store_path() -> Path:
    override = os.getenv("OPENSRE_WIZARD_STORE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / "opensre.json"


def get_memory_dir() -> Path:
    override = os.getenv(OPENSRE_MEMORY_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return session_home() / "memory"


def ensure_opensre_tmp_dir() -> Path:
    OPENSRE_TMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        OPENSRE_TMP_DIR.chmod(0o700)
    return OPENSRE_TMP_DIR


__all__ = [
    "INTEGRATIONS_STORE_PATH",
    "MEMBERS_DIR_NAME",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "ORGS_DIR_NAME",
    "PROJECT_ROOT",
    "REPO_ROOT",
    "SYNTHETIC_SCENARIOS_DIR",
    "UnsafePathSegmentError",
    "ensure_opensre_tmp_dir",
    "get_memory_dir",
    "get_store_path",
    "integrations_store_path",
    "opensre_home",
    "session_home",
]
