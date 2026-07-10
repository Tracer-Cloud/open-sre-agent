"""Infer tool-native layer contracts from repository layout."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners._paths import iter_source_files
from tools.architecture_issue_tool.scanners.import_graph.models import (
    ForbiddenDirectRule,
    LayerContract,
)
from tools.architecture_issue_tool.scanners.import_graph.resolve import unit_for_file

_ARCH_ROOTS = ("src", "lib", "cmd", "internal", "pkg", "app", "packages")

_BOTTOM_UNITS = frozenset(
    {"internal", "infra", "platform", "pkg", "config", "core", "domain", "lib", "shared"}
)
_TOP_UNITS = frozenset(
    {"app", "api", "handlers", "cmd", "web", "ui", "main", "surfaces", "gateway", "server"}
)
_MIDDLE_UNITS = frozenset({"services", "tools", "integrations", "features", "modules"})


def _discover_units(clone_root: Path) -> set[str]:
    units: set[str] = set()
    for path in iter_source_files(clone_root):
        unit = unit_for_file(clone_root, path)
        if unit:
            units.add(unit)
    return units


def _tier_for_unit(unit: str) -> int:
    lowered = unit.lower()
    if lowered in _BOTTOM_UNITS:
        return 0
    if lowered in _TOP_UNITS:
        return 2
    if lowered in _MIDDLE_UNITS:
        return 1
    return 1


def _build_layers(units: set[str]) -> tuple[tuple[str, ...], ...]:
    tiers: dict[int, list[str]] = {0: [], 1: [], 2: []}
    for unit in sorted(units):
        tiers[_tier_for_unit(unit)].append(unit)
    return tuple(tuple(tiers[index]) for index in (0, 1, 2) if tiers[index])


def _default_forbidden_direct(units: set[str]) -> tuple[ForbiddenDirectRule, ...]:
    rules: list[ForbiddenDirectRule] = []
    for pattern in (
        ForbiddenDirectRule(source="infra", targets=("app", "api", "handlers", "surfaces")),
        ForbiddenDirectRule(source="internal", targets=("app", "api", "handlers")),
        ForbiddenDirectRule(source="core", targets=("surfaces", "gateway", "app")),
        ForbiddenDirectRule(source="platform", targets=("surfaces", "app", "api")),
    ):
        if pattern.source in units:
            targets = tuple(target for target in pattern.targets if target in units)
            if targets:
                rules.append(ForbiddenDirectRule(source=pattern.source, targets=targets))
    return tuple(rules)


def infer_generic_contract(clone_root: Path) -> LayerContract:
    """Build a language-agnostic contract from directory layout heuristics."""
    units = _discover_units(clone_root)
    layers = _build_layers(units)
    roots = tuple(root for root in _ARCH_ROOTS if (clone_root / root).is_dir())
    if not roots:
        roots = ("",)
    return LayerContract(
        name="generic-inferred",
        roots=roots,
        layers=layers,
        forbidden_direct=_default_forbidden_direct(units),
        allowlist=(),
    )
