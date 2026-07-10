"""Import-layer violation scanning via polyglot tree-sitter import graphs."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.scanners.import_graph.scan import scan_import_graph


def scan_import_violations(
    clone_root: Path,
    *,
    strict_layers: bool = True,
    include_baselines: bool = False,
) -> tuple[list[ArchitectureViolation], list[str]]:
    """Scan *clone_root* for layer and direct import violations."""
    return scan_import_graph(
        clone_root,
        strict_layers=strict_layers,
        include_baselines=include_baselines,
    )
