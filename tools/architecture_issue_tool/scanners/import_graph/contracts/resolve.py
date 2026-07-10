"""Pick the best layer contract for a cloned repository."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.contracts.generic import (
    infer_generic_contract,
)
from tools.architecture_issue_tool.scanners.import_graph.contracts.profiles import get_profile
from tools.architecture_issue_tool.scanners.import_graph.models import LayerContract

_PROFILE_MARKERS: tuple[tuple[str, str], ...] = (
    ("layered-monorepo", "src"),
    ("layered-monorepo", "packages"),
)


def resolve_contract(clone_root: Path) -> LayerContract:
    """Resolve a tool-native contract without reading clone CI config."""
    for profile_name, marker in _PROFILE_MARKERS:
        if (clone_root / marker).is_dir():
            profile = get_profile(profile_name)
            if profile is not None:
                return profile.contract
    return infer_generic_contract(clone_root)
