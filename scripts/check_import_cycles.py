"""Detect first-party module-load import cycles via Tarjan's SCC.

Walks every first-party Python module in the repo, builds an import
graph from **top-level** ``import`` / ``from ... import`` statements
only (function-level lazy imports are intentional runtime breaks and
not counted), then reports any strongly-connected component of size
> 1, plus any single-module self-loop.

Used by ``make check-cycles`` locally and by CI to prevent regressions
after the initial cycle-breaking PR landed.

Exit codes:
    0 — zero cycles found
    1 — at least one cycle found (output lists every SCC + its edges)
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

# Top-level first-party package names. Edit when a new top-level package
# is added at the repo root.
_FIRST_PARTY_ROOTS: tuple[str, ...] = (
    "agent",
    "agents",
    "analytics",
    "auth",
    "cli",
    "config",
    "constants",
    "core",
    "deployment",
    "entrypoints",
    "fleet_monitoring",
    "guardrails",
    "integrations",
    "masking",
    "nodes",
    "observability",
    "pipeline",
    "platform",
    "remote",
    "sandbox",
    "scheduler",
    "services",
    "state",
    "tools",
    "types",
    "utils",
)


def _top_level_imports(source: str) -> set[str]:
    """Return first-party module paths imported at the module top level.

    Function-bodies, class-bodies, conditional / try-except wrappers all
    count as top-level if they are direct module statements — the only
    imports skipped are those nested **inside a function or class body**.
    A lazy ``from X import Y`` inside a function does not deadlock at
    module load, so it should not be flagged as a cycle.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()

    def _add(module_path: str) -> None:
        top = module_path.split(".", 1)[0]
        if top in _FIRST_PARTY_ROOTS:
            names.add(module_path)

    # Walk every top-level statement, including ones inside ``if`` /
    # ``try`` blocks (TYPE_CHECKING guards, optional-dep wrappers, etc.).
    # Do NOT descend into FunctionDef / AsyncFunctionDef / ClassDef.
    def _walk_top(body: Iterable[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                _add(node.module)
            elif isinstance(node, ast.If):
                _walk_top(node.body)
                _walk_top(node.orelse)
            elif isinstance(node, ast.Try):
                _walk_top(node.body)
                for handler in node.handlers:
                    _walk_top(handler.body)
                _walk_top(node.orelse)
                _walk_top(node.finalbody)
            elif isinstance(node, ast.With | ast.AsyncWith):
                _walk_top(node.body)

    _walk_top(tree.body)
    return names


def _build_graph(root: Path) -> dict[str, set[str]]:
    """Build the first-party module-level import graph rooted at ``root``."""
    graph: dict[str, set[str]] = defaultdict(set)
    for pkg in _FIRST_PARTY_ROOTS:
        pkg_path = root / pkg
        if not pkg_path.exists():
            continue
        for py in pkg_path.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            module = ".".join(py.with_suffix("").relative_to(root).parts)
            module = module.removesuffix(".__init__")
            source = py.read_text(encoding="utf-8")
            graph[module].update(_top_level_imports(source))
    return graph


def _tarjan_sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return every strongly-connected component of size > 1, plus any
    single-module self-loop."""
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        index[v] = counter[0]
        lowlink[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack[v] = True

        for w in graph.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w):
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                component.append(w)
                if w == v:
                    break
            if len(component) > 1 or component and component[0] in graph.get(component[0], ()):
                sccs.append(component)

    sys.setrecursionlimit(10000)
    for vertex in list(graph.keys()):
        if vertex not in index:
            strongconnect(vertex)

    return sccs


def _format_scc(scc: list[str], graph: dict[str, set[str]]) -> str:
    """Format an SCC for human-readable output: members + edges within."""
    members = sorted(scc)
    in_scc = set(scc)
    edges: list[str] = []
    for module in members:
        for target in sorted(graph.get(module, ())):
            if target in in_scc and target != module:
                edges.append(f"    {module} -> {target}")

    lines = [f"  Modules ({len(scc)}):"]
    lines.extend(f"    - {m}" for m in members)
    if edges:
        lines.append("  Edges within SCC:")
        lines.extend(edges)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    graph = _build_graph(root)
    sccs = _tarjan_sccs(graph)

    if not sccs:
        print(f"No import cycles found across {len(graph)} first-party modules.")
        return 0

    print(f"FAIL: {len(sccs)} import cycle(s) found across {len(graph)} first-party modules.")
    for i, scc in enumerate(sorted(sccs, key=lambda s: -len(s)), 1):
        print(f"\n## SCC #{i} ({len(scc)} module{'s' if len(scc) > 1 else ''}):")
        print(_format_scc(scc, graph))
    print(
        "\nBreak the cycle by replacing top-level imports with explicit submodule "
        "imports (``import pkg.sub as sub`` rather than ``from pkg import sub``), "
        "or by introducing a port/protocol module that both sides depend on."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
