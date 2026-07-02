"""Read access to the persisted remote-agent config — usable from layers below ``surfaces/``.

The wizard JSON file lives at the path returned by
:func:`config.constants.get_store_path`. Several layers below ``surfaces/`` —
notably ``platform/deployment/`` — need to *read* the persisted remote URLs and
the remote ops scope. They cannot import from ``surfaces.cli.wizard.store``
without violating the layering contract in ``surfaces/__init__.py``.

This module hosts the read-side functions in ``config/`` so the layering
holds. ``surfaces.cli.wizard.store`` re-exports them under their original
names so existing callers (and unit-test mocks that patch the surfaces path)
keep working.

The write-side wizard helpers stay in ``surfaces/cli/wizard/store.py`` because
they belong to the wizard surface workflow.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from config.constants import get_store_path

_VERSION = 1
_EMPTY_CONFIG: dict[str, Any] = {
    "version": _VERSION,
    "wizard": {},
    "targets": {},
    "probes": {},
}


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    """Read the wizard JSON safely; return an empty config on any failure."""
    store_path = path or get_store_path()
    if not store_path.exists():
        return deepcopy(_EMPTY_CONFIG)
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(_EMPTY_CONFIG)
    if not isinstance(data, dict):
        return deepcopy(_EMPTY_CONFIG)
    return data


def load_named_remotes(path: Path | None = None) -> dict[str, str]:
    """Return all named remotes as ``{name: url}``."""
    data = _load_raw(path)
    remotes: dict[str, Any] = data.get("remote", {}).get("remotes", {})
    return {k: str(v.get("url", "")) for k, v in remotes.items() if v.get("url")}


def load_remote_ops_config(path: Path | None = None) -> dict[str, str | None]:
    """Return persisted remote ops config values."""
    data = _load_raw(path)
    remote_data = data.get("remote", {})
    if not isinstance(remote_data, dict):
        return {"provider": None, "project": None, "service": None}
    return {
        "provider": str(remote_data.get("provider") or "") or None,
        "project": str(remote_data.get("project") or "") or None,
        "service": str(remote_data.get("service") or "") or None,
    }


__all__ = [
    "load_named_remotes",
    "load_remote_ops_config",
]
