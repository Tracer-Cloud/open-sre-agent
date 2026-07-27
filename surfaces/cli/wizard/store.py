"""Persistent storage for quickstart wizard selections."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.constants import get_store_path as _get_store_path_from_config
from config.remote_store import (
    load_named_remotes as _load_named_remotes_from_config,
)
from config.remote_store import (
    load_remote_ops_config as _load_remote_ops_config_from_config,
)

_VERSION = 1
_EMPTY_CONFIG = {"version": _VERSION, "wizard": {}, "targets": {}, "probes": {}}


def get_store_path() -> Path:
    """Default path to the wizard config file.

    Re-exports ``config.constants.get_store_path`` so layers below
    ``surfaces/`` can import the path without crossing the surfaces
    boundary. The function lives in ``config/`` because that's where
    ``OPENSRE_HOME_DIR`` is defined; this module preserves the legacy
    ``from surfaces.cli.wizard.store import get_store_path`` import
    path that callers already use.
    """
    return _get_store_path_from_config()


def _load_raw(path: Path | None = None) -> dict[str, Any]:
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


def load_local_config(path: Path | None = None) -> dict[str, Any]:
    """Return the persisted wizard payload for the current user."""
    return _load_raw(path)


def save_local_config(
    *,
    wizard_mode: str,
    provider: str,
    model: str,
    api_key_env: str,
    model_env: str,
    probes: dict[str, dict[str, object]],
    auth_method: str | None = None,
    path: Path | None = None,
) -> Path:
    """Persist the local wizard configuration to disk."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    timestamp = datetime.now(UTC).isoformat()
    data["version"] = _VERSION
    data["wizard"] = {
        "mode": wizard_mode,
        "configured_target": "local",
        "updated_at": timestamp,
    }
    targets = data.setdefault("targets", {})
    targets["local"] = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "model_env": model_env,
        "updated_at": timestamp,
    }
    if auth_method:
        targets["local"]["auth_method"] = auth_method
    data["probes"] = probes

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return store_path


def update_local_llm_selection(
    *,
    provider: str,
    model: str,
    api_key_env: str = "",
    model_env: str = "",
    auth_method: str | None = None,
    path: Path | None = None,
) -> Path:
    """Merge LLM provider/model into the wizard store without resetting other fields."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    timestamp = datetime.now(UTC).isoformat()
    targets = data.setdefault("targets", {})
    local = targets.setdefault("local", {})
    local["provider"] = provider
    local["model"] = model
    local["api_key_env"] = api_key_env
    local["model_env"] = model_env
    if auth_method:
        local["auth_method"] = auth_method
    else:
        local.pop("auth_method", None)
    local["updated_at"] = timestamp
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return store_path


def load_remote_url(path: Path | None = None) -> str | None:
    """Return the persisted remote agent URL, or ``None`` if not configured."""
    data = _load_raw(path)
    url: str | None = data.get("remote", {}).get("url") or None
    return url


# Re-exported from ``config.remote_store`` so layers below ``surfaces/`` can read this
# without crossing the surfaces boundary. Existing callers + test mocks that target
# ``surfaces.cli.wizard.store.load_named_remotes`` keep working unchanged.
load_named_remotes = _load_named_remotes_from_config


def save_named_remote(
    name: str,
    url: str,
    *,
    set_active: bool = False,
    source: str = "manual",
    path: Path | None = None,
) -> None:
    """Save a named remote endpoint."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remote_section = data.setdefault("remote", {})
    remotes = remote_section.setdefault("remotes", {})
    remotes[name] = {
        "url": url,
        "source": source,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if set_active:
        remote_section["url"] = url
        remote_section["active_name"] = name
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def delete_named_remote(name: str, path: Path | None = None) -> None:
    """Remove a named remote entry and clear the active URL if it was the active one."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remote_section = data.get("remote", {})
    remotes: dict[str, Any] = remote_section.get("remotes", {})
    if name not in remotes:
        return
    removed_url = remotes.pop(name, {}).get("url")
    if removed_url and remote_section.get("url") == removed_url:
        remote_section.pop("url", None)
        remote_section.pop("active_name", None)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# Re-exported from ``config.remote_store`` — see the comment above
# ``load_named_remotes`` for why this name resolves through ``config/``.
load_remote_ops_config = _load_remote_ops_config_from_config


def save_remote_ops_config(
    *,
    provider: str,
    project: str | None,
    service: str | None,
    path: Path | None = None,
) -> None:
    """Persist remote ops provider scope to the store."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remote_data = data.setdefault("remote", {})
    remote_data["provider"] = provider
    if project:
        remote_data["project"] = project
    else:
        remote_data.pop("project", None)
    if service:
        remote_data["service"] = service
    else:
        remote_data.pop("service", None)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
