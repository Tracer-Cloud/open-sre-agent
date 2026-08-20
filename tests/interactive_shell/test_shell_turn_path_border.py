"""The shell's shipped turn path runs through the turn host, not its own agent.

The REPL uses :class:`platform.turn_host.turn_handler.TurnHandler` — the same
handler Slack, Telegram and Discord use. ``build_shell_agent`` still exists so
tests can get an agent without standing up a host, but nothing shipped may
call it: that would rebuild the agent beside the host and let the surfaces
drift apart again.

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
_TURN_HANDLER = "TurnHandler"
_TURN_HANDLER_MODULE = "platform.turn_host.turn_handler"
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
    """True when the module imports, names, or passes the shell agent builder.

    Catches ``from … import build_shell_agent``, bare / attribute calls, and
    indirect uses such as ``factory = module.build_shell_agent`` (no call yet).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == _SHELL_AGENT_BUILDER for alias in node.names):
                return True
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == _SHELL_AGENT_BUILDER
            or isinstance(node, ast.Name)
            and node.id == _SHELL_AGENT_BUILDER
        ):
            return True
    return False


def _turn_handler_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to ``TurnHandler`` via ``from … import TurnHandler [as X]``."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != _TURN_HANDLER_MODULE:
            continue
        for alias in node.names:
            if alias.name == _TURN_HANDLER:
                aliases.add(alias.asname or alias.name)
    return aliases


def _annotation_names(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def _is_turn_handler_call(node: ast.AST, handler_aliases: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in handler_aliases
    )


def _handler_bound_names(tree: ast.AST, handler_aliases: set[str]) -> set[str]:
    """Names that are typed or constructed as ``TurnHandler`` in this module."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if _annotation_names(arg.annotation) & handler_aliases:
                    bound.add(arg.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _annotation_names(node.annotation) & handler_aliases:
                bound.add(node.target.id)
            if node.value is not None and _is_turn_handler_call(node.value, handler_aliases):
                bound.add(node.target.id)
        elif isinstance(node, ast.Assign) and _is_turn_handler_call(node.value, handler_aliases):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    return bound


def _calls_turn_handler_run(tree: ast.AST) -> bool:
    """True when ``run`` is invoked on a ``TurnHandler`` name or construction.

    A bare ``something.run(...)`` is not enough — the receiver must be bound to
    the imported turn host, or be ``TurnHandler(...).run(...)``.
    """
    handler_aliases = _turn_handler_aliases(tree)
    if not handler_aliases:
        return False
    bound = _handler_bound_names(tree, handler_aliases)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "run":
            continue
        receiver = func.value
        if isinstance(receiver, ast.Name) and receiver.id in bound:
            return True
        if _is_turn_handler_call(receiver, handler_aliases):
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
    imports_host = bool(_turn_handler_aliases(tree))
    runs_host = _calls_turn_handler_run(tree)

    # Assert
    assert imports_host, f"{_TURN_PATH} must import TurnHandler — it is the shared turn"
    assert runs_host, (
        f"{_TURN_PATH} must call TurnHandler.run (or handler.run where handler is "
        "a TurnHandler), not an unrelated .run()"
    )


def test_builder_guard_sees_an_attribute_passed_as_a_factory() -> None:
    tree = ast.parse(
        "import surfaces.interactive_shell.runtime.shell_agent as sa\n"
        "factory = sa.build_shell_agent\n"
    )
    assert _references_builder(tree) is True


def test_builder_guard_ignores_unrelated_names() -> None:
    tree = ast.parse("factory = build_other_agent\n")
    assert _references_builder(tree) is False


def test_turn_host_guard_rejects_an_unrelated_run_call() -> None:
    tree = ast.parse(
        "from platform.turn_host.turn_handler import TurnHandler\n"
        "def execute_shell_turn():\n"
        "    agent = object()\n"
        "    return agent.run()\n"
    )
    assert _calls_turn_handler_run(tree) is False


def test_turn_host_guard_accepts_handler_run_on_a_typed_parameter() -> None:
    tree = ast.parse(
        "from platform.turn_host.turn_handler import TurnHandler\n"
        "def execute_shell_turn(handler: TurnHandler | None = None):\n"
        "    return handler.run('hi', None, None, None)\n"
    )
    assert _calls_turn_handler_run(tree) is True
