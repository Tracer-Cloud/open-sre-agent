"""Persistent storage for quickstart wizard selections."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.constants import OPENSRE_HOME_DIR

_VERSION = 1
_EMPTY_CONFIG = {"version": _VERSION, "wizard": {}, "targets": {}, "probes": {}}


def get_store_path() -> Path:
    """Return the default wizard config path."""
    return OPENSRE_HOME_DIR / "opensre.json"


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


def _remote_section(data: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
    remote = data.get("remote")
    if isinstance(remote, dict):
        return remote
    if create:
        remote = {}
        data["remote"] = remote
        return remote
    return {}


def _remote_entries(remote_section: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
    remotes = remote_section.get("remotes")
    if isinstance(remotes, dict):
        return remotes
    if create:
        remotes = {}
        remote_section["remotes"] = remotes
        return remotes
    return {}


def save_local_config(
    *,
    wizard_mode: str,
    provider: str,
    model: str,
    api_key_env: str,
    model_env: str,
    probes: dict[str, dict[str, object]],
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
    data["probes"] = probes

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return store_path


def load_remote_url(path: Path | None = None) -> str | None:
    """Return the persisted remote agent URL, or ``None`` if not configured."""
    data = _load_raw(path)
    url: str | None = _remote_section(data).get("url") or None
    return url


def save_remote_url(url: str, path: Path | None = None) -> None:
    """Persist the remote agent URL to the store."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    _remote_section(data, create=True)["url"] = url
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_named_remotes(path: Path | None = None) -> dict[str, str]:
    """Return all named remotes as ``{name: url}``."""
    data = _load_raw(path)
    remotes = _remote_entries(_remote_section(data))
    named_remotes: dict[str, str] = {}
    for name, entry in remotes.items():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if url:
            named_remotes[name] = str(url)
    return named_remotes


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
    remote_section = _remote_section(data, create=True)
    remotes = _remote_entries(remote_section, create=True)
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


def set_active_remote(name: str, path: Path | None = None) -> str:
    """Switch the active remote to *name*. Returns the URL."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remotes = _remote_entries(_remote_section(data))
    entry = remotes.get(name)
    if not isinstance(entry, dict) or not entry.get("url"):
        raise KeyError(f"No remote named '{name}'")

    url: str = str(entry["url"])
    remote_section = _remote_section(data, create=True)
    remote_section["url"] = url
    remote_section["active_name"] = name
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return url


def load_active_remote_name(path: Path | None = None) -> str | None:
    """Return the name of the currently active remote, or ``None``."""
    data = _load_raw(path)
    name: str | None = _remote_section(data).get("active_name") or None
    return name


def load_remote_ops_config(path: Path | None = None) -> dict[str, str | None]:
    """Return persisted remote ops config values."""
    data = _load_raw(path)
    remote_data = _remote_section(data)
    return {
        "provider": str(remote_data.get("provider") or "") or None,
        "project": str(remote_data.get("project") or "") or None,
        "service": str(remote_data.get("service") or "") or None,
    }


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
    remote_data = _remote_section(data, create=True)
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
