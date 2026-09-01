"""Guards the acyclic edge between the tracker lifecycle and property builders.

CodeQL alert #2263 (``py/cyclic-import``) fired when both
``investigation_tracker`` and ``event_properties`` reached back into each other.
The shared record and merge helpers were moved to ``investigation_tracker_types``
so both modules import only from that leaf. This test pins that direction.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ANALYTICS = Path(__file__).resolve().parents[2] / "infrastructure" / "analytics"


def _module_level_imports(module_file: Path) -> set[str]:
    tree = ast.parse(module_file.read_text())
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_event_properties_does_not_import_investigation_tracker() -> None:
    imports = _module_level_imports(_ANALYTICS / "event_properties.py")
    assert "infrastructure.analytics.investigation_tracker" not in imports


def test_capture_does_not_import_investigation_tracker() -> None:
    imports = _module_level_imports(_ANALYTICS / "capture.py")
    assert "infrastructure.analytics.investigation_tracker" not in imports


def test_investigation_tracker_types_is_a_leaf() -> None:
    imports = _module_level_imports(_ANALYTICS / "investigation_tracker_types.py")
    forbidden = {
        "infrastructure.analytics.investigation_tracker",
        "infrastructure.analytics.event_properties",
        "infrastructure.analytics.capture",
    }
    assert not (imports & forbidden)


def test_leaf_exports_shared_symbols() -> None:
    from infrastructure.analytics import investigation_tracker_types as leaf

    assert hasattr(leaf, "InvestigationTracker")
    assert hasattr(leaf, "_with_investigation_loop_metrics")
    assert hasattr(leaf, "_resolve_investigation_loop_metrics")
