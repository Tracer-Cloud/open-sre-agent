"""Import graph construction."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners._paths import iter_source_files
from tools.architecture_issue_tool.scanners.import_graph.languages.extract import (
    extract_raw_imports,
)
from tools.architecture_issue_tool.scanners.import_graph.models import ImportGraph, ResolvedEdge
from tools.architecture_issue_tool.scanners.import_graph.package_index import build_package_index
from tools.architecture_issue_tool.scanners.import_graph.resolve import (
    resolve_import_to_unit,
    unit_for_file,
)


def build_import_graph(clone_root: Path) -> tuple[ImportGraph, int, int]:
    """Build a directed graph of architectural unit edges.

    Returns ``(graph, raw_import_count, resolved_edge_count_before_dedupe)``.
    """
    root = clone_root.resolve()
    graph = ImportGraph()
    known_units: set[str] = set()
    for path in iter_source_files(root):
        unit = unit_for_file(root, path)
        if unit:
            known_units.add(unit)

    package_index = build_package_index(root)
    raw_imports = extract_raw_imports(root)
    resolved_attempts = 0
    for raw in raw_imports:
        source_path = root / raw.source_file
        source_unit = unit_for_file(root, source_path)
        if not source_unit:
            continue
        target_unit = resolve_import_to_unit(
            root,
            source_path,
            raw.import_spec,
            known_units,
            package_index=package_index,
        )
        if target_unit is None or target_unit == source_unit:
            continue
        resolved_attempts += 1
        graph.add_edge(
            ResolvedEdge(
                source_unit=source_unit,
                target_unit=target_unit,
                source_file=raw.source_file,
                line=raw.line,
                import_spec=raw.import_spec,
                language=raw.language,
            )
        )
    return graph, len(raw_imports), resolved_attempts
