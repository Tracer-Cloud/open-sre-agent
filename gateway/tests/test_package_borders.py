"""AST borders for gateway/core · transports · web package DAG.

Pinned rules (see ``gateway/AGENTS.md``):

* Chat transports are peers — none imports another.
* ``gateway.web`` never imports ``gateway.transports``.
* ``gateway.core`` never imports surfaces, except the composition root
  ``gateway.core.runtime.manager``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_TRANSPORTS = (
    "gateway.transports.slack",
    "gateway.transports.discord",
    "gateway.transports.telegram",
)

_CORE_SURFACE_ALLOWLIST = frozenset(
    {
        # Composition root — wires chat workers + web server.
        "gateway/core/runtime/manager.py",
    }
)


def _python_files(package: str) -> list[Path]:
    root = REPO_ROOT / Path(*package.split("."))
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offenders(package: str, banned_prefixes: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in _python_files(package):
        for name in _imported_modules(path):
            if any(name == p or name.startswith(f"{p}.") for p in banned_prefixes):
                found.append(f"{path.relative_to(REPO_ROOT)} → {name}")
    return found


def _peer_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for left in _TRANSPORTS:
        for right in _TRANSPORTS:
            if left != right:
                pairs.append((left, right))
    return pairs


def test_chat_transport_peers_never_import_each_other() -> None:
    offenders: list[str] = []
    for package, banned in _peer_pairs():
        offenders.extend(_offenders(package, (banned,)))
    assert offenders == [], "Chat transport peer import:\n" + "\n".join(offenders)


def test_web_surface_never_imports_chat_transports() -> None:
    offenders = _offenders("gateway.web", _TRANSPORTS)
    assert offenders == [], "Web surface reached into a chat transport:\n" + "\n".join(offenders)


def test_core_leaves_never_import_surfaces_except_manager() -> None:
    banned = (*_TRANSPORTS, "gateway.web")
    offenders: list[str] = []
    for path in _python_files("gateway.core"):
        rel = str(path.relative_to(REPO_ROOT))
        if rel in _CORE_SURFACE_ALLOWLIST:
            continue
        for name in _imported_modules(path):
            if any(name == p or name.startswith(f"{p}.") for p in banned):
                offenders.append(f"{rel} → {name}")
    assert offenders == [], "Core leaf imported a surface:\n" + "\n".join(offenders)


def test_approvals_module_imports_no_transport() -> None:
    path = REPO_ROOT / "gateway" / "core" / "runtime" / "approvals.py"
    imported = _imported_modules(path)
    leaked = [n for n in imported if any(n == p or n.startswith(f"{p}.") for p in _TRANSPORTS)]
    assert leaked == [], f"approvals.py imports transports: {leaked}"
