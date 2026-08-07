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


def _discover_transport_packages() -> tuple[str, ...]:
    """Every chat transport package on disk.

    Discovered rather than listed: a hand-maintained list silently exempts a
    newly added transport from every border rule below.
    """
    root = REPO_ROOT / "gateway" / "transports"
    return tuple(
        f"gateway.transports.{child.name}"
        for child in sorted(root.iterdir())
        if child.is_dir() and (child / "__init__.py").is_file()
    )


_TRANSPORTS = _discover_transport_packages()

_CHANNELS_COMPOSER = "gateway/core/runtime/manager.py"

_TRANSPORT_STARTUP_MODULES = frozenset(f"{package}.startup" for package in _TRANSPORTS)


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


_QUARANTINED_HANDLER_NAMES = frozenset(
    {
        "ConcurrencyLimitedTurnHandler",
        "gated_callback",
    }
)


def test_concurrency_limited_handler_stays_out_of_production_gateway() -> None:
    """Wave C4: capacity wrapper is tests-only; production uses GatewayTurnHandler(gate=)."""
    offenders: list[str] = []
    for path in _python_files("gateway"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _QUARANTINED_HANDLER_NAMES:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)} → from {node.module} import {alias.name}"
                        )
            elif isinstance(node, ast.Name) and node.id in _QUARANTINED_HANDLER_NAMES:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → name {node.id}")
    assert offenders == [], (
        "quarantined ConcurrencyLimitedTurnHandler leaked into production:\n" + "\n".join(offenders)
    )


def test_production_gateway_never_subclasses_core_agent() -> None:
    """Wave D4: gateway hosts HeadlessAgent; it must not own a core.agent.Agent subclass.

    Chat turns reuse SessionAgentPool → build_default_headless_agent. A production
    ``class …(Agent)`` under gateway/ would be a second brain beside the harness.
    """
    offenders: list[str] = []
    for path in _python_files("gateway"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        agent_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "core.agent",
                "core.agent.agent",
            }:
                for alias in node.names:
                    if alias.name == "Agent":
                        agent_aliases.add(alias.asname or "Agent")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"core.agent", "core.agent.agent"}:
                        # ``import core.agent`` — subclass would be Attribute on that name.
                        agent_aliases.add(f"{alias.asname or alias.name}.Agent")
        if not agent_aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                base_name = _ast_name(base)
                if base_name in agent_aliases or base_name == "Agent":
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)} → class {node.name}({base_name})"
                    )
    assert offenders == [], "production gateway subclasses core.agent.Agent:\n" + "\n".join(
        offenders
    )


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _ast_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def test_gateway_chat_never_builds_core_agent_or_precomputed_tools() -> None:
    """Wave D4: chat uses SessionAgentPool → HeadlessAgent; no gateway-owned Agent."""
    offenders: list[str] = []
    for path in _python_files("gateway"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod == "core.agent" or (
                    mod.startswith("core.agent.") and not mod.startswith("core.agent_harness")
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} → import {mod}")
                for alias in node.names:
                    if alias.name == "build_agent":
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)} → from {mod} import build_agent"
                        )
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "build_agent":
                    offenders.append(f"{path.relative_to(REPO_ROOT)} → build_agent(...)")
                if isinstance(func, ast.Attribute) and func.attr == "build_agent":
                    offenders.append(f"{path.relative_to(REPO_ROOT)} → *.build_agent(...)")
            elif isinstance(node, ast.keyword) and node.arg == "precomputed_action_tools":
                offenders.append(f"{path.relative_to(REPO_ROOT)} → precomputed_action_tools=")
    assert offenders == [], (
        "gateway must not own a ReAct Agent or precomputed tools:\n" + "\n".join(offenders)
    )


def test_every_transport_package_is_registered_in_the_chat_table() -> None:
    """A transport package on disk must appear in the registry, and vice versa.

    Adding a transport used to mean editing the manager; now it means adding a
    ``TransportSpec`` row. This fails if someone ships the package without the
    row (the transport never starts) or the row without the package.
    """
    # Arrange / Act.
    from gateway.channels.chat import TRANSPORTS

    on_disk = {package.rsplit(".", 1)[-1] for package in _TRANSPORTS}
    registered = {spec.name.value for spec in TRANSPORTS}

    # Assert.
    assert on_disk == registered, (
        f"transport packages and registry rows disagree: "
        f"only on disk={sorted(on_disk - registered)}, "
        f"only registered={sorted(registered - on_disk)}"
    )
