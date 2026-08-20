"""The shell's shipped turn path runs through the turn host, not its own agent.

#5209 pointed the REPL at :class:`platform.turn_host.turn_handler.TurnHandler` —
the same handler Slack, Telegram and Discord use. ``build_shell_agent`` still
exists so tests can get an agent without standing up a host, but nothing shipped
may call it: that would rebuild the agent beside the host and let the surfaces
drift apart again, which is the whole thing Wave E closed.

AST, not text search, so a commented-out or renamed-import call cannot keep this
green. The allowlist is compared exactly, so it can only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.product_sources import product_python_files

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Same list the Makefile type-checks and lints (``PYTHON_SOURCE_PATHS``).
_PRODUCT_PACKAGES = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "platform",
    "surfaces",
    "tools",
)

_SHELL_AGENT_BUILDER = "build_shell_agent"
_TURN_PATH = Path("surfaces/interactive_shell/runtime/shell_turn_execution.py")

#: The module that defines the builder, plus its own ``__all__``. Nothing else.
_DEFINING_MODULE = Path("surfaces/interactive_shell/runtime/shell_agent.py")

#: Shipped modules still reaching for the builder. Empty: only tests call it.
_ALLOWED_CALLERS: frozenset[str] = frozenset()


def _product_files() -> list[Path]:
    files: list[Path] = []
    for package in _PRODUCT_PACKAGES:
        files.extend(product_python_files(REPO_ROOT / package))
    return sorted(files)


def _references_builder(tree: ast.AST) -> bool:
    """True when the module imports or calls the shell agent builder."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == _SHELL_AGENT_BUILDER for alias in node.names):
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == _SHELL_AGENT_BUILDER:
                return True
            if isinstance(func, ast.Attribute) and func.attr == _SHELL_AGENT_BUILDER:
                return True
    return False


def test_no_shipped_module_builds_a_second_shell_agent() -> None:
    # Arrange: every product module except the one that defines the builder.
    candidates = [
        path for path in _product_files() if path.relative_to(REPO_ROOT) != _DEFINING_MODULE
    ]

    # Act
    callers = {
        str(path.relative_to(REPO_ROOT))
        for path in candidates
        if _references_builder(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    }

    # Assert: exact both ways, so a stale allowlist entry fails too.
    assert callers == set(_ALLOWED_CALLERS), (
        "the shell agent builder belongs to tests; shipped code goes through the "
        f"turn host instead:\nunexpected: {sorted(callers - set(_ALLOWED_CALLERS))}\n"
        f"stale allowlist: {sorted(set(_ALLOWED_CALLERS) - callers)}"
    )


def test_the_shell_turn_path_runs_the_turn_host() -> None:
    # Arrange
    path = REPO_ROOT / _TURN_PATH
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    # Act
    imports_host = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "platform.turn_host.turn_handler"
        and any(alias.name == "TurnHandler" for alias in node.names)
        for node in ast.walk(tree)
    )
    runs_host = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        for node in ast.walk(tree)
    )

    # Assert
    assert imports_host, f"{_TURN_PATH} must import TurnHandler — it is the shared turn"
    assert runs_host, f"{_TURN_PATH} must call the handler's run(), not build an agent"
