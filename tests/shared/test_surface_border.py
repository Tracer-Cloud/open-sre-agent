"""The two terminal surfaces do not depend on each other.

``surfaces/cli`` and ``surfaces/interactive_shell`` are peers: what both need
lives below them (``surfaces/shared``, ``config``, ``core``, ``platform``).
Each direction's remaining imports are pinned as an exact allowlist so it can
only shrink — a new cross-surface import fails immediately, and an entry no
longer imported must be removed. Underscore-prefixed names never cross.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_api import python_sources

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = "surfaces.cli"
SHELL = "surfaces.interactive_shell"

#: CLI modules the shell still imports.
_SHELL_IMPORTS_FROM_CLI: frozenset[str] = frozenset(
    {
        # The shell documents the CLI's commands for the model.
        "surfaces.cli.app",
    }
)

#: Shell modules the CLI still imports.
_CLI_IMPORTS_FROM_SHELL: frozenset[str] = frozenset(
    {
        # Launching the REPL — the composition point; belongs in the entrypoint.
        "surfaces.interactive_shell",
        # Terminal primitives both surfaces render with — belong in surfaces/shared.
        "surfaces.interactive_shell.ui.layout",
        "surfaces.interactive_shell.ui.health",
        "surfaces.interactive_shell.ui.feedback",
        "surfaces.interactive_shell.ui.components.rendering",
        "surfaces.interactive_shell.ui.components.key_reader",
        "surfaces.interactive_shell.ui.components.banner_art",
        "surfaces.interactive_shell.ui.agents.agents_view",
        "surfaces.interactive_shell.ui.stream_renderer",
        # Harness port installation — belongs in bootstrap/.
        "surfaces.interactive_shell.ui.output.boundary",
        # Slash-command adapter for the gateway entry.
        "surfaces.interactive_shell.runtime.slash_adapter",
    }
)


def _imports_of(tree: ast.AST, package: str) -> tuple[set[str], set[str]]:
    """Return ``(modules, private_names)`` imported from ``package`` in ``tree``."""
    modules: set[str] = set()
    private: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == package or node.module.startswith(package + "."):
                modules.add(node.module)
                private.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(package + "."):
                    modules.add(alias.name)
    return modules, private


def _cross_imports(source_package: str, target_package: str) -> tuple[set[str], set[str]]:
    root = REPO_ROOT.joinpath(*source_package.split("."))
    modules: set[str] = set()
    private: set[str] = set()
    for path in python_sources(root):
        found_modules, found_private = _imports_of(
            ast.parse(path.read_text(encoding="utf-8")), target_package
        )
        modules |= found_modules
        private |= found_private
    return modules, private


def _assert_matches(imported: set[str], allowlist: frozenset[str], *, edge: str) -> None:
    added = sorted(imported - allowlist)
    assert added == [], (
        f"{edge} imports not on the allowlist: {added}. "
        "Move the shared code below both surfaces (surfaces/shared, config, core, platform)."
    )
    stale = sorted(allowlist - imported)
    assert stale == [], (
        f"{edge} no longer imports {stale}; remove them so the allowlist keeps shrinking."
    )


def test_shell_imports_from_the_cli_only_what_the_allowlist_names() -> None:
    modules, private = _cross_imports(SHELL, CLI)
    _assert_matches(modules, _SHELL_IMPORTS_FROM_CLI, edge="interactive_shell → cli")
    assert sorted(private) == []


def test_cli_imports_from_the_shell_only_what_the_allowlist_names() -> None:
    modules, private = _cross_imports(CLI, SHELL)
    _assert_matches(modules, _CLI_IMPORTS_FROM_SHELL, edge="cli → interactive_shell")
    assert sorted(private) == []
