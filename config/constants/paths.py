"""Filesystem roots for OpenSRE data.

- :data:`OPENSRE_HOME_DIR` — host root (gateway pid/log, install catalog, analytics).
- :func:`opensre_home` — the organization's context root. In a deployed Slack
  service this is the mounted S3 Files volume named by ``OPENSRE_CONTEXT_ROOT``,
  which the infrastructure already chroots to one organization. On a laptop it
  is the host root.
- :func:`session_home` — one Slack user's own conversation root inside the org.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import tempfile
from pathlib import Path

from config.constants.memory import OPENSRE_MEMORY_DIR_ENV
from config.constants.organization import organization_id
from config.constants.tenancy import INTEGRATIONS_STORE_PATH_ENV
from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from config.scope_context import current_scope

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT

SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "rds_postgres"

OPENSRE_HOME_DIR = Path.home() / ".opensre"
OPENSRE_TMP_DIR = Path(tempfile.gettempdir()) / "opensre"

# Set by the deployed Slack service to the mounted, org-chrooted volume.
CONTEXT_ROOT_ENV = "OPENSRE_CONTEXT_ROOT"

ORGS_DIR_NAME = "orgs"
USERS_DIR_NAME = "users"

# Ids reach the filesystem; reject segments that could escape their directory.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class UnsafePathSegmentError(ValueError):
    """Raised when an id would escape its context directory."""


class ContextRootOwnerMismatchError(RuntimeError):
    """Raised when a turn's org does not own the mounted context root.

    The mount is chrooted to one organization; serving a turn for a different
    org would write its data into another org's volume. Fail closed.
    """


@functools.cache
def _warn_unmounted_org_once(org_id: str) -> None:
    """Warn (once per org) that a bound org is falling back to local disk.

    On a deployed silo that means the ephemeral container filesystem — memory,
    sessions, and integration credentials are lost on the next task restart.
    Intended for local multi-org dev, so this warns rather than fails.
    """
    logger.warning(
        "[paths] org %s is bound but %s is unset; using local disk. On a deployed "
        "silo this is ephemeral and lost on restart.",
        org_id,
        CONTEXT_ROOT_ENV,
    )


def _safe_segment(value: str, *, label: str) -> str:
    # "." and ".." match the character class but walk out of the directory.
    if not _SAFE_PATH_SEGMENT.match(value) or value.strip(".") == "":
        raise UnsafePathSegmentError(f"unsafe {label} for a context path: {value!r}")
    return value


def host_home() -> Path:
    """Root for files owned by the machine, never by a customer.

    Read through this rather than importing :data:`OPENSRE_HOME_DIR` directly,
    so the root stays redirectable in one place.
    """
    return OPENSRE_HOME_DIR


def opensre_home() -> Path:
    """The organization's context root, or the host home when unbound.

    The mount named by ``OPENSRE_CONTEXT_ROOT`` belongs to one organization, so
    it is used only for a turn that names that organization. Anything unbound —
    boot-time integration reads, the CLI — stays on the host root rather than
    writing into a customer's volume. Chat transports bind an org scope and
    therefore use the mount (or ``orgs/<id>/`` nest) when configured.

    Without the mount, a bound org principal nests under
    ``~/.opensre/orgs/<org_id>/`` so several organizations can be exercised on
    one machine.
    """
    scope = current_scope()
    if scope is None or scope.principal.kind != "org":
        return OPENSRE_HOME_DIR
    mounted_root = os.getenv(CONTEXT_ROOT_ENV, "").strip()
    if mounted_root:
        # The mount is chrooted to exactly one org. If this deployment declares
        # its owner, refuse a turn for any other org — otherwise a
        # multi-workspace gateway could write org B's data into org A's volume.
        # Fail closed.
        silo_owner = organization_id()
        if not silo_owner:
            raise ContextRootOwnerMismatchError(
                f"{CONTEXT_ROOT_ENV} is set but no organization is configured for this "
                "deployment; refusing to write a customer's data to an unidentified volume"
            )
        if scope.principal.id != silo_owner:
            raise ContextRootOwnerMismatchError(
                f"context root belongs to {silo_owner!r} but this turn is owned by "
                f"{scope.principal.id!r}; refusing to cross organizations on a shared mount"
            )
        return Path(mounted_root).expanduser()
    _warn_unmounted_org_once(scope.principal.id)
    org_id = _safe_segment(scope.principal.id, label="principal id")
    return OPENSRE_HOME_DIR / ORGS_DIR_NAME / org_id


def session_home() -> Path:
    """One Slack user's own conversation root inside the org.

    Each member keeps the private history a laptop user has, at
    ``<org root>/users/<slack_user_id>/``. Unbound callers get the org root, so
    a terminal run stays flat.
    """
    org_root = opensre_home()
    scope = current_scope()
    if scope is None or scope.principal.kind != "org":
        return org_root
    actor_id = _safe_segment(scope.actor.id, label="actor id")
    return org_root / USERS_DIR_NAME / actor_id


def integrations_store_path() -> Path:
    """Integrations store path (shared by every member of the org).

    A deployed silo overrides this with ``OPENSRE_INTEGRATIONS_STORE_PATH``,
    which the control plane points at ephemeral container storage: that silo
    serves one tenant, and its credentials are re-hydrated from the tenant's
    Secrets Manager blob at every boot rather than kept on the shared mount.
    """
    override = os.getenv(INTEGRATIONS_STORE_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
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


def get_work_items_dir() -> Path:
    override = os.getenv(OPENSRE_WORK_ITEMS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return session_home() / "work_items"


def ensure_opensre_tmp_dir() -> Path:
    OPENSRE_TMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        OPENSRE_TMP_DIR.chmod(0o700)
    return OPENSRE_TMP_DIR


__all__ = [
    "CONTEXT_ROOT_ENV",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "ORGS_DIR_NAME",
    "USERS_DIR_NAME",
    "PROJECT_ROOT",
    "REPO_ROOT",
    "SYNTHETIC_SCENARIOS_DIR",
    "ContextRootOwnerMismatchError",
    "UnsafePathSegmentError",
    "ensure_opensre_tmp_dir",
    "get_memory_dir",
    "get_store_path",
    "get_work_items_dir",
    "host_home",
    "integrations_store_path",
    "opensre_home",
    "session_home",
]
