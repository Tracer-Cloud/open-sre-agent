"""Offline status probes for the compact launch banner.

Counts only — no skill bodies, no vendor SDKs, no prompt_toolkit. The chips are
decorative startup chrome; loading the action-skill harness or full catalog
health graph here made first paint pay hundreds of milliseconds for two integers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchStatus:
    """Counts displayed beside the OpenSRE mark."""

    skill_count: int
    integration_count: int


# ``surfaces/shared/terminal/banner/banner_state.py`` → package root (opensre/).
_PACKAGE_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_SKILLS_DIR = _PACKAGE_ROOT / "core" / "agent_harness" / "prompts" / "skills"


def _count_bundled_skill_files(directory: Path) -> int:
    """Count discoverable skill recipes without importing the skill loader.

    Mirrors ``loader._iter_skill_paths`` (package dirs then top-level ``*.md``)
    and dedupes by skill name the way ``list_action_skills`` does — without
    reading file bodies or importing harness package ``__init__`` graphs.
    """
    if not directory.is_dir():
        return 0
    names: set[str] = set()

    def _add(name: str) -> None:
        key = name.replace("_", "-").lower()
        if key:
            names.add(key)

    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "SKILL.md").is_file() or (child / f"{child.name}.md").is_file():
            _add(child.name)
    for path in sorted(directory.glob("*.md")):
        _add(path.stem)
    return len(names)


def _count_loaded_skills() -> int:
    """Return the number of bundled action-agent skills (filesystem only)."""
    try:
        return _count_bundled_skill_files(_BUNDLED_SKILLS_DIR)
    except Exception:
        return 0


def _integrations_store_file() -> Path:
    """Best-effort path to ``integrations.json`` without importing the store stack."""
    override = os.getenv("OPENSRE_INTEGRATIONS_STORE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    home = os.getenv("OPENSRE_HOME", "").strip()
    root = Path(home).expanduser() if home else Path.home() / ".opensre"
    return root / "integrations.json"


def _count_active_store_services(path: Path) -> int:
    """Count active services in the local store JSON (no filelock / migration)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    records = data.get("integrations", []) if isinstance(data, dict) else []
    if not isinstance(records, list):
        return 0
    services: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("status", "active")).strip().lower() != "active":
            continue
        service = str(record.get("service", "")).strip().lower()
        if service:
            services.add(service)
    return len(services)


def _count_configured_integrations() -> int:
    """Prefer the catalog SoT; fall back to a cheap store JSON count.

    The catalog path matches env + store (banner must not disagree with
    ``/integrations``). When that import is too heavy or fails, the store file
    alone still lights the chip.
    """
    try:
        from integrations.catalog import configured_integration_services

        return len(configured_integration_services())
    except Exception:
        try:
            return _count_active_store_services(_integrations_store_file())
        except Exception:
            return 0


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        integration_count=_count_configured_integrations(),
    )


__all__ = ["LaunchStatus", "load_launch_status"]
