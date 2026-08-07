"""AST borders for gateway/core · channels · transports · web package DAG.

Pinned rules (see ``gateway/AGENTS.md``):

* Chat transports are peers — none imports another.
* ``gateway.web`` never imports ``gateway.transports`` or ``gateway.channels``.
* ``gateway.core`` never imports chat transports or ``gateway.web``.
* Only ``gateway.core.runtime.manager`` may import ``gateway.channels``.
* ``gateway.transports.*`` never imports ``gateway.channels`` or ``gateway.web``.
* ``gateway.channels`` may import peer ``*.startup`` (and ``gateway.web``); peers
  must not import channels.
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

_CHANNELS_COMPOSER = "gateway/core/runtime/manager.py"

_TRANSPORT_STARTUP_MODULES = frozenset(
    {
        "gateway.transports.slack.startup",
        "gateway.transports.discord.startup",
        "gateway.transports.telegram.startup",
    }
)


def _python_files(package: str) -> list[Path]:
    root = REPO_ROOT / Path(*package.split("."))
    if root.is_file() and root.suffix == ".py":
        return [root]
    if not root.is_dir():
        return []
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


def test_web_surface_never_imports_chat_transports_or_channels() -> None:
    offenders = _offenders("gateway.web", (*_TRANSPORTS, "gateway.channels"))
    assert offenders == [], "Web surface reached into transports/channels:\n" + "\n".join(offenders)


def test_core_never_imports_chat_transports_or_web() -> None:
    """Surfaces are composed via ``gateway.channels``, not from core leaves."""
    banned = (*_TRANSPORTS, "gateway.web", "gateway.transports")
    offenders = _offenders("gateway.core", banned)
    assert offenders == [], "Core imported a surface directly:\n" + "\n".join(offenders)


def test_only_manager_imports_channels_module() -> None:
    offenders: list[str] = []
    for path in _python_files("gateway.core"):
        rel = str(path.relative_to(REPO_ROOT))
        for name in _imported_modules(path):
            names_channels = name == "gateway.channels" or name.startswith("gateway.channels.")
            if names_channels and rel != _CHANNELS_COMPOSER:
                offenders.append(f"{rel} → {name}")
    assert offenders == [], "Non-manager core imported gateway.channels:\n" + "\n".join(offenders)


def test_transports_never_import_channels_or_web() -> None:
    offenders: list[str] = []
    for package in _TRANSPORTS:
        offenders.extend(_offenders(package, ("gateway.channels", "gateway.web")))
    # Also scan transports package root (no registry composer left there).
    offenders.extend(_offenders("gateway.transports", ("gateway.channels", "gateway.web")))
    # Deduplicate paths that appear both as package and via rglob of parent.
    offenders = sorted(set(offenders))
    assert offenders == [], "Transport peer imported channels/web:\n" + "\n".join(offenders)


def test_channels_only_imports_peer_startup_not_transport_internals() -> None:
    """Channels may compose via ``*.startup``; deeper peer modules stay private."""
    offenders: list[str] = []
    for path in _python_files("gateway.channels"):
        rel = str(path.relative_to(REPO_ROOT))
        for name in _imported_modules(path):
            if not any(name == p or name.startswith(f"{p}.") for p in _TRANSPORTS):
                continue
            if name in _TRANSPORT_STARTUP_MODULES:
                continue
            offenders.append(f"{rel} → {name}")
    assert offenders == [], "channels imported non-startup transport modules:\n" + "\n".join(
        offenders
    )


def test_approvals_module_imports_no_transport() -> None:
    path = REPO_ROOT / "gateway" / "core" / "runtime" / "approvals.py"
    imported = _imported_modules(path)
    leaked = [n for n in imported if any(n == p or n.startswith(f"{p}.") for p in _TRANSPORTS)]
    assert leaked == [], f"approvals.py imports transports: {leaked}"


def _executable_surface_references(path: Path) -> list[str]:
    """Return imports and runtime string literals naming a ``surfaces`` module.

    Docstrings are excluded: prose describing the boundary is documentation,
    not a dependency.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found: list[str] = []
    for name in _imported_modules(path):
        if name == "surfaces" or name.startswith("surfaces."):
            found.append(f"import {name}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or node in docstrings:
            continue
        if isinstance(node.value, str) and node.value.startswith("surfaces."):
            found.append(f"line {node.lineno}: {node.value!r}")
    return found


def test_gateway_never_names_a_surfaces_module_in_executable_code() -> None:
    """``surfaces`` is a peer package, and a spawned module path is still a dependency.

    The daemon used to hardcode ``python -m surfaces.cli.gateway_entry`` in its
    subprocess argv. Import-linter cannot see a dependency spelled as a string
    literal, so the cycle stayed invisible: a surface starts the daemon, and the
    daemon starts a surface back. Each surface now passes its own argv.
    """
    # Arrange / Act: scan every non-test gateway module.
    offenders: list[str] = []
    for path in _python_files("gateway"):
        if "tests" in path.parts:
            continue
        for reference in _executable_surface_references(path):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {reference}")

    # Assert.
    assert offenders == [], "gateway depends on a surfaces module:\n" + "\n".join(offenders)
