"""Read and write ``~/.opensre/config.yml``.

One owner for the on-disk settings file, so the CLI's ``opensre config`` and any
feature that persists its own section agree on the path, the parser, and the
nested-key rules. Lives in ``config/`` rather than a surface because lower tiers
read the file too.

Environment variables take precedence over anything stored here; this module
only reports what is on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.constants import paths

_FILE_NAME = "config.yml"


class LocalSettingsError(RuntimeError):
    """The settings file exists but cannot be used."""


def local_settings_path() -> Path:
    """Resolved per call, so a redirected home (tests, silos) is honoured."""
    return paths.OPENSRE_HOME_DIR / _FILE_NAME


def load_local_settings() -> dict[str, Any]:
    """Parsed settings file, or an empty mapping when there is none."""
    path = local_settings_path()
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise LocalSettingsError(f"could not parse {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LocalSettingsError(f"invalid {path}: expected a mapping at the top level")
    return data


def read_section(name: str) -> dict[str, Any]:
    """One top-level section, or an empty mapping when absent or malformed."""
    section = load_local_settings().get(name)
    return section if isinstance(section, dict) else {}


def save_local_settings(data: dict[str, Any]) -> None:
    import yaml

    path = local_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def update_section(name: str, values: dict[str, Any]) -> None:
    """Merge ``values`` into one section, leaving every other section intact."""
    data = load_local_settings()
    section = data.get(name)
    merged = dict(section) if isinstance(section, dict) else {}
    merged.update(values)
    data[name] = merged
    save_local_settings(data)


def set_nested_key(data: dict[str, Any], dotted_key: str, value: Any) -> dict[str, Any]:
    """Set ``a.b.c`` inside ``data``, creating intermediate mappings."""
    parts = [part for part in dotted_key.split(".") if part]
    if not parts:
        raise LocalSettingsError("a settings key cannot be empty")
    cursor: dict[str, Any] = data
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value
    return data


__all__ = [
    "LocalSettingsError",
    "load_local_settings",
    "local_settings_path",
    "read_section",
    "save_local_settings",
    "set_nested_key",
    "update_section",
]
