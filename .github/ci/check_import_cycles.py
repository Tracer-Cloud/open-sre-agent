"""Detect first-party module-load import cycles via Tarjan's SCC.

Walks every first-party Python module in the repo, builds an import
graph from **top-level** ``import`` / ``from ... import`` statements
only (function-level lazy imports are intentional runtime breaks and
not counted), then reports any strongly-connected component of size
> 1, plus any single-module self-loop.

Used by ``make check-imports`` (via ``check_imports``) locally and by CI.

Exit codes:
    0 — zero cycles found
    1 — at least one cycle found (output lists every SCC + its edges)


How to break a cycle
====================

Two patterns work. Pick by what consumers do with the name.

1. ``import pkg.sub as sub`` (preferred when consumers monkeypatch)

   Switch ::

       from pkg.sub import name        # binds ``name`` at import time
       ...
       name(args)                       # uses the bound reference

   to ::

       import pkg.sub as sub           # imports the submodule directly
       ...
       sub.name(args)                   # attribute lookup at call time

   Both break the static cycle (no edge from the consumer back to the
   ``pkg`` package). The second form keeps attribute-lookup semantics,
   which matters whenever consumers monkeypatch ``pkg.sub.name`` in
   tests — the patched attribute IS looked up at each call. The first
   form binds the name at import time and ignores later patching.

2. Port / Protocol (when the cycle crosses architectural layers)

   When the cycle is ``layerA <-> layerB`` (e.g. analytics ↔ sentry,
   integrations ↔ services), neither side should depend on the other
   directly. Extract a third module — a ``Protocol`` or a small
   abstract dataclass — that both depend on, and inject the concrete
   implementation at startup. See the verifier plugin-registry
   refactor (``integrations/verification/registry.py``) for the
   canonical example.

Avoid ``from pkg import sub`` (where ``sub`` is a submodule of ``pkg``)
inside that ``pkg`` itself or any of its children — that's the form
this script flags. It triggers ``pkg``'s ``__init__`` even when you
just want the submodule, and re-export patterns in ``__init__`` close
the loop.

Function-local imports are NOT flagged. They're a legitimate Python
pattern for startup-cost deferral (heavy modules in click subcommand
bodies), optional dependencies, and conditional / platform-specific
code paths. Use sparingly — keep top-level the default — and comment
the *why* so future readers don't mistake them for cycle workarounds.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

# Directories at the repo root that are never first-party import roots.
_SKIP_ROOT_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "docs",
        "infra",
        "opensre.egg-info",
        "packaging",
        "tests",
        "venv",
    }
)


@lru_cache(maxsize=1)
def discover_first_party_roots(repo_root: Path | None = None) -> tuple[str, ...]:
    """Return top-level package names that contain importable Python code."""
    root = repo_root or Path(__file__).resolve().parents[2]
    names: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in _SKIP_ROOT_DIRS:
            continue
        if not any(child.rglob("*.py")):
            continue
        names.append(child.name)
    return tuple(names)


def _top_level_imports(source: str, *, first_party_roots: frozenset[str]) -> set[str]:
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
        if top in first_party_roots:
            names.add(module_path)

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
            elif isinstance(node, ast.Try | ast.TryStar):
                # ``ast.TryStar`` (Python 3.11+, ``try/except*``) shares
                # the same handler/orelse/finalbody shape as ``ast.Try``.
                _walk_top(node.body)
                for handler in node.handlers:
                    _walk_top(handler.body)
                _walk_top(node.orelse)
                _walk_top(node.finalbody)
            elif isinstance(node, ast.With | ast.AsyncWith):
                _walk_top(node.body)

    _walk_top(tree.body)
    return names


def _build_graph(root: Path, first_party_roots: tuple[str, ...]) -> dict[str, set[str]]:
    """Build the first-party module-level import graph rooted at ``root``."""
    roots = frozenset(first_party_roots)
    graph: dict[str, set[str]] = defaultdict(set)
    for pkg in first_party_roots:
        pkg_path = root / pkg
        if not pkg_path.exists():
            continue
        for py in pkg_path.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            module = ".".join(py.with_suffix("").relative_to(root).parts)
            module = module.removesuffix(".__init__")
            source = py.read_text(encoding="utf-8")
            graph[module].update(_top_level_imports(source, first_party_roots=roots))
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
            # ``while True`` above guarantees ``component`` is non-empty here.
            if len(component) > 1 or component[0] in graph.get(component[0], ()):
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
    is_self_loop = len(scc) == 1
    edges: list[str] = []
    for module in members:
        for target in sorted(graph.get(module, ())):
            if target not in in_scc:
                continue
            # Single-module self-loops have only the self-edge; show it
            # explicitly so the developer knows which import closes the
            # loop. Multi-module SCCs hide self-edges to keep the diff
            # focused on the cross-module edges that close the cycle.
            if target == module and not is_self_loop:
                continue
            edges.append(f"    {module} -> {target}")

    lines = [f"  Modules ({len(scc)}):"]
    lines.extend(f"    - {m}" for m in members)
    if is_self_loop and not edges:
        lines.append("  Self-import detected (module imports itself at top level).")
    elif edges:
        lines.append("  Edges within SCC:")
        lines.extend(edges)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    del argv
    root = Path(__file__).resolve().parents[2]
    first_party_roots = discover_first_party_roots(root)
    graph = _build_graph(root, first_party_roots)
    sccs = _tarjan_sccs(graph)

    if not sccs:
        print(
            f"No import cycles found across {len(graph)} first-party modules "
            f"({len(first_party_roots)} roots)."
        )
        return 0

    print(f"FAIL: {len(sccs)} import cycle(s) found across {len(graph)} first-party modules.")
    for i, scc in enumerate(sorted(sccs, key=lambda s: -len(s)), 1):
        print(f"\n## SCC #{i} ({len(scc)} module{'s' if len(scc) > 1 else ''}):")
        print(_format_scc(scc, graph))
    print(
        "\nTo break a cycle, prefer:\n"
        "    import pkg.sub as sub\n"
        "    ...\n"
        "    sub.name(args)\n"
        "over:\n"
        "    from pkg.sub import name\n"
        "    ...\n"
        "    name(args)\n"
        "\n"
        "Both fix the static cycle; only the first keeps attribute-lookup\n"
        "semantics so tests that monkeypatch ``pkg.sub.name`` still work.\n"
        "\n"
        "For cross-layer cycles, introduce a Protocol/port both sides\n"
        "depend on (see ``integrations/verification/registry.py`` for the\n"
        "canonical example).\n"
        "\n"
        "Full pattern reference: docstring at the top of this script."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
