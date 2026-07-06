"""Repo-wide layering contract tests mirroring docs/ARCHITECTURE.md (T-10).

Encodes the seven first-party package tiers as pytest regressions so
``make test-cov`` catches new layer violations without requiring contributors
to remember ``make check-imports``. Reuses the CI graph checkers in
``.github/ci/`` rather than duplicating AST logic.

Fixes #3544 (T-11).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

_CI_DIR = Path(__file__).resolve().parents[3] / ".github" / "ci"
_REPO_ROOT = _CI_DIR.parents[1]
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import (  # noqa: E402
    _BASELINE_IGNORES,
    find_direct_violations,
    find_nested_direct_violations,
)
from check_import_cycles import (  # noqa: E402
    _build_graph,
    discover_first_party_roots,
)
from check_import_cycles import (
    main as check_import_cycles_main,
)
from check_imports import main as check_imports_main  # noqa: E402

_FIRST_PARTY_ROOTS = frozenset(discover_first_party_roots(_REPO_ROOT))
_OTHER_FIRST_PARTY = _FIRST_PARTY_ROOTS - {"config"}
_ARCHITECTURE_DOC = _REPO_ROOT / "docs" / "ARCHITECTURE.md"
_STRICT_IMPORTLINTER = _REPO_ROOT / ".importlinter.strict"


def _config_upward_baseline() -> frozenset[str]:
    """Known config -> other-package edges from ``.importlinter.strict`` (T-4 debt)."""
    if not _STRICT_IMPORTLINTER.is_file():
        return frozenset()
    edges: set[str] = set()
    for line in _STRICT_IMPORTLINTER.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("config.") and " -> " in stripped:
            edges.add(stripped)
    return frozenset(edges)


def _config_import_edge(module_path: Path, target_module: str) -> str:
    rel_module = ".".join(module_path.with_suffix("").relative_to(_REPO_ROOT).parts)
    rel_module = rel_module.removesuffix(".__init__")
    return f"{rel_module} -> {target_module}"


def _repo_python_files(package: str) -> Iterable[Path]:
    pkg_path = _REPO_ROOT / package
    if not pkg_path.exists():
        return
    for py in sorted(pkg_path.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        yield py


def _imports_other_first_party_from_config(source: str) -> list[tuple[str, int]]:
    """Return (target_module, lineno) for config imports of non-config first-party."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    offenders: list[tuple[str, int]] = []

    def _is_other_first_party(module_path: str) -> bool:
        root = module_path.split(".", 1)[0]
        return root in _OTHER_FIRST_PARTY

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_other_first_party(alias.name):
                    offenders.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            if _is_other_first_party(node.module):
                offenders.append((node.module, node.lineno))
    return offenders


def _tier1_peer_cross_baseline() -> frozenset[str]:
    """Known surfaces <-> gateway cross-imports (T-4 debt + CLI gateway command)."""
    edges: set[str] = set()
    if _STRICT_IMPORTLINTER.is_file():
        for line in _STRICT_IMPORTLINTER.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if " -> " not in stripped:
                continue
            source, target = stripped.split(" -> ", 1)
            source_root = source.split(".", 1)[0]
            target_root = target.split(".", 1)[0]
            if {source_root, target_root} == {"surfaces", "gateway"}:
                edges.add(stripped)
    for edge in _BASELINE_IGNORES:
        source, target = edge.split(" -> ", 1)
        source_root = source.split(".", 1)[0]
        target_root = target.split(".", 1)[0]
        if {source_root, target_root} == {"surfaces", "gateway"}:
            edges.add(edge)
    return frozenset(edges)


def _collect_peer_import_offenders(
    *,
    package: str,
    forbidden_root: str,
) -> list[str]:
    offenders: list[str] = []
    forbidden_prefix = f"{forbidden_root}."
    for path in _repo_python_files(package):
        rel_module = ".".join(path.with_suffix("").relative_to(_REPO_ROOT).parts)
        rel_module = rel_module.removesuffix(".__init__")
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name == forbidden_root or name.startswith(forbidden_prefix):
                        edge = f"{rel_module} -> {name}"
                        offenders.append(edge)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                module = node.module
                if module == forbidden_root or module.startswith(forbidden_prefix):
                    edge = f"{rel_module} -> {module}"
                    offenders.append(edge)
    return offenders


def test_no_first_party_import_cycles() -> None:
    """ARCHITECTURE.md: dependencies must not form import cycles."""
    assert check_import_cycles_main() == 0


def test_config_does_not_import_first_party_packages() -> None:
    """Tier 4: ``config`` imports no other first-party package (minus T-4 debt baseline)."""
    baseline = _config_upward_baseline()
    offenders: list[str] = []
    for path in _repo_python_files("config"):
        rel = path.relative_to(_REPO_ROOT)
        for module, lineno in _imports_other_first_party_from_config(
            path.read_text(encoding="utf-8"),
        ):
            edge = _config_import_edge(path, module)
            if edge in baseline:
                continue
            offenders.append(f"{rel}:{lineno} -> {module}")
    assert not offenders, (
        "config/ has new upward first-party imports — add to .importlinter.strict "
        "only with a linked burn-down issue, or refactor into a lower layer "
        "(see docs/ARCHITECTURE.md Tier 4). Offenders:\n"
        + "\n".join(f"  {item}" for item in offenders)
    )
    assert len(baseline) >= 5


def test_surfaces_and_gateway_are_independent_peers() -> None:
    """Tier 1: ``surfaces`` and ``gateway`` must not import each other (minus baseline)."""
    baseline = _tier1_peer_cross_baseline()
    surface_offenders = [
        edge
        for edge in _collect_peer_import_offenders(package="surfaces", forbidden_root="gateway")
        if edge not in baseline
    ]
    gateway_offenders = [
        edge
        for edge in _collect_peer_import_offenders(package="gateway", forbidden_root="surfaces")
        if edge not in baseline
    ]
    assert not surface_offenders, (
        "surfaces/ has new gateway/ imports — add to .importlinter.strict with a "
        "linked burn-down issue (Tier 1 peer rule):\n"
        + "\n".join(f"  {item}" for item in surface_offenders)
    )
    assert not gateway_offenders, (
        "gateway/ has new surfaces/ imports — add to check_direct_imports baseline "
        "with a linked burn-down issue (Tier 1 peer rule):\n"
        + "\n".join(f"  {item}" for item in gateway_offenders)
    )
    assert len(baseline) >= 1


def test_no_unbaselined_forbidden_direct_imports_module_level() -> None:
    """Tier 2–3: no new upward direct imports at module top level."""
    first_party_roots = discover_first_party_roots(_REPO_ROOT)
    graph = _build_graph(_REPO_ROOT, first_party_roots)
    violations = find_direct_violations(graph)
    assert violations == [], (
        "Unexpected module-level direct import violations — update _BASELINE_IGNORES "
        "only with linked burn-down issues:\n" + "\n".join(f"  {v.edge}" for v in violations)
    )


def test_no_unbaselined_forbidden_direct_imports_nested() -> None:
    """Lazy imports cannot bypass the layering contract."""
    first_party_roots = discover_first_party_roots(_REPO_ROOT)
    violations = find_nested_direct_violations(_REPO_ROOT, first_party_roots)
    assert violations == [], (
        "Unexpected nested direct import violations — update _NESTED_BASELINE_IGNORES "
        "only with linked burn-down issues:\n"
        + "\n".join(f"  {v.edge} (line {v.lineno})" for v in violations)
    )


def test_transitive_layer_contract_strict() -> None:
    """Full transitive tier stack enforced by ``.importlinter.strict``."""
    assert check_imports_main(["--strict"]) == 0


def test_architecture_doc_layer_table_present() -> None:
    """Doc/test drift guard: ARCHITECTURE.md still documents the layer contract."""
    text = _ARCHITECTURE_DOC.read_text(encoding="utf-8")
    required_phrases = (
        "dependencies point downward",
        "`surfaces`",
        "`gateway`",
        "`tools`",
        "`integrations`",
        "`core`",
        "`platform`",
        "`config`",
        "core ⟷ platform",
    )
    missing = [phrase for phrase in required_phrases if phrase not in text]
    assert not missing, f"docs/ARCHITECTURE.md missing expected phrases: {missing}"


@pytest.mark.parametrize(
    ("source_module", "target_module", "forbidden"),
    [
        ("integrations.grafana.tools", "tools.tool_decorator", True),
        ("tools.fleet_monitoring", "surfaces.cli.commands.doctor", True),
        ("platform.analytics.provider", "surfaces.cli.wizard.store", True),
        ("tools.investigation.capability", "core.agent.Agent", False),
        ("surfaces.cli.commands.doctor", "tools.investigation.capability", False),
    ],
)
def test_forbidden_direct_edge_contract(
    source_module: str,
    target_module: str,
    forbidden: bool,
) -> None:
    """Synthetic graphs prove _FORBIDDEN_DIRECT matches ARCHITECTURE.md tier rules."""
    graph = {source_module: {target_module}}
    violations = find_direct_violations(graph, baseline_ignores=frozenset())
    is_forbidden = any(v.edge == f"{source_module} -> {target_module}" for v in violations)
    assert is_forbidden == forbidden
