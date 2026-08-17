"""The interactive shell does not depend on the CLI.

``surfaces/cli`` composes the shell (``run_repl``, its renderers); the shell
must not reach back into the CLI. This test pins the remaining back-edge as an
exact allowlist so it can only shrink: a new ``surfaces.cli`` import fails
immediately, and an entry no longer imported must be removed. Underscore-
prefixed names are never allowed across the border.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_api import python_sources

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_ROOT = REPO_ROOT / "surfaces" / "interactive_shell"
CLI_PACKAGE = "surfaces.cli"

#: CLI modules the shell still imports. Each is a leaf the CLI owns today; the
#: target home is noted so the entry leaves when the code does.
_ALLOWED_CLI_IMPORTS: frozenset[str] = frozenset(
    {
        # The shell documents the CLI's commands for the model — the one edge
        # that describes the CLI itself; the reference could be injected by run_repl.
        "surfaces.cli.app",
        # Provider catalog and env sync — the wizard's provider table, used by
        # /model; belongs below both surfaces (core/llm provider catalog).
        "surfaces.cli.wizard.config",
        "surfaces.cli.wizard.env_sync",
        "surfaces.cli.llm_auth.providers",
        "surfaces.cli.llm_auth.service",
    }
)


def _cli_imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Return ``(modules, private_names)`` imported from ``surfaces.cli`` in ``tree``."""
    modules: set[str] = set()
    private: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == CLI_PACKAGE or node.module.startswith(CLI_PACKAGE + "."):
                modules.add(node.module)
                private.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == CLI_PACKAGE or alias.name.startswith(CLI_PACKAGE + "."):
                    modules.add(alias.name)
    return modules, private


def _shell_cli_imports() -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    private: set[str] = set()
    for path in python_sources(SHELL_ROOT):
        found_modules, found_private = _cli_imports(ast.parse(path.read_text(encoding="utf-8")))
        modules |= found_modules
        private |= found_private
    return modules, private


def test_shell_imports_from_the_cli_only_what_the_allowlist_names() -> None:
    modules, _ = _shell_cli_imports()
    added = sorted(modules - _ALLOWED_CLI_IMPORTS)
    assert added == [], (
        f"surfaces/interactive_shell imports CLI modules not on the allowlist: {added}. "
        "Move the shared code below both surfaces (surfaces/shared, config, core, platform)."
    )
    stale = sorted(_ALLOWED_CLI_IMPORTS - modules)
    assert stale == [], (
        f"surfaces/interactive_shell no longer imports {stale}; remove them so the allowlist keeps shrinking."
    )


def test_shell_never_imports_private_cli_names() -> None:
    _, private = _shell_cli_imports()
    assert sorted(private) == []
