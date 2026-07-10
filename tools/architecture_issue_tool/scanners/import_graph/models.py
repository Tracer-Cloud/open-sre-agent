"""Data models for polyglot import-graph scanning."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawImport:
    """One import/include extracted from source syntax."""

    source_file: str
    import_spec: str
    line: int
    language: str


@dataclass(frozen=True)
class ResolvedEdge:
    """Directed edge between architectural units inside the clone."""

    source_unit: str
    target_unit: str
    source_file: str
    line: int
    import_spec: str
    language: str


@dataclass(frozen=True)
class ForbiddenDirectRule:
    """Forbidden direct cross-unit edge pattern."""

    source: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class LayerContract:
    """Tool-native layer contract for a repository layout."""

    name: str
    roots: tuple[str, ...]
    layers: tuple[tuple[str, ...], ...]
    forbidden_direct: tuple[ForbiddenDirectRule, ...] = ()
    allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractProfile:
    """Named contract profile shipped with the architecture tool."""

    name: str
    contract: LayerContract


@dataclass
class ImportGraph:
    """Directed adjacency list over architectural units."""

    units: set[str] = field(default_factory=set)
    edges: list[ResolvedEdge] = field(default_factory=list)
    adjacency: dict[str, set[str]] = field(default_factory=dict)

    def add_edge(self, edge: ResolvedEdge) -> None:
        self.units.add(edge.source_unit)
        self.units.add(edge.target_unit)
        self.edges.append(edge)
        self.adjacency.setdefault(edge.source_unit, set()).add(edge.target_unit)
