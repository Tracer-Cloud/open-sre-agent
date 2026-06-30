"""Layering boundary test: core-facing packages must not import from ``cli``.

Core (``core/domain/``, ``tools/investigation/``) reports progress, prints debug
output, and renders investigation headers/footers through the ports defined in
:mod:`platform.observability`. Reaching into ``cli.*`` directly couples the
domain/orchestration layer to the REPL's specific renderer and breaks headless /
non-TTY callers.

See issue #35 and the introduction of ``build_*_provider`` /
``set_*`` injection helpers in ``platform/observability/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE_PACKAGES: tuple[Path, ...] = (
    Path("core/domain"),
    Path("tools/investigation"),
    Path("platform/observability"),
)
_CORE_ONLY_PACKAGES: tuple[Path, ...] = (Path("core/domain"),)
_CORE_RUNTIME_MODULES: tuple[Path, ...] = (
    Path("core/__init__.py"),
    Path("core/agent.py"),
    Path("core/context_budget.py"),
    Path("core/events.py"),
    Path("core/execution.py"),
    Path("core/llm_invoke_errors.py"),
    Path("core/messages/__init__.py"),
    Path("core/messages/build_runtime_messages.py"),
    Path("core/messages/convert_to_llm_messages.py"),
    Path("core/messages/ensure_runtime_messages.py"),
    Path("core/messages/runtime_message_types.py"),
    Path("core/provider.py"),
    Path("core/types.py"),
)
# Anything imported from a forbidden prefix by a core module is a
# layering violation. Inverted dependency: core defines ports, CLI /
# vendor service packages implement them at the boundary.
#
# Forbidden prefixes:
# - ``cli`` — closed by #35 (observability ports). Core never
#   needs CLI internals; if you think you do, file a new
#   observability port instead.
# - ``integrations.tracer`` — closed by #36
#   (``integrations.port`` ``fetch_remote_integrations``). Hosted LLM
#   provider code lives in ``core.llm`` and remains core runtime
#   capability access rather than integration-coupled transport.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "cli",
    "integrations.tracer",
)


def _core_modules() -> list[Path]:
    files: list[Path] = []
    for root in _CORE_PACKAGES:
        files.extend(p for p in root.glob("**/*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _python_modules_under(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in root.glob("**/*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _core_only_modules() -> list[Path]:
    return sorted(_python_modules_under(_CORE_ONLY_PACKAGES) + list(_CORE_RUNTIME_MODULES))


def _imported_modules(source: str) -> set[str]:
    """Module-paths every ``import``/``from`` statement names in ``source``."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — out of scope
                continue
            if node.module:
                names.add(node.module)
    return names


@pytest.mark.parametrize("module_path", _core_modules(), ids=str)
def test_core_module_does_not_import_forbidden_layers(module_path: Path) -> None:
    """Core modules must avoid forbidden boundary packages.

    Use ports instead — ``platform.observability`` for progress/debug/display,
    ``integrations.port`` for remote integrations — and register
    concrete adapters via ``install_product_adapters``.
    """
    source = module_path.read_text(encoding="utf-8")
    imports = _imported_modules(source)
    leaks = {
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES)
    }
    assert not leaks, (
        f"{module_path} imports forbidden module(s) {sorted(leaks)} — route through a "
        "port (``platform.observability.*`` or ``integrations.port``) and register "
        "adapters via ``install_product_adapters``."
    )


@pytest.mark.parametrize("module_path", _core_only_modules(), ids=str)
def test_core_does_not_import_investigation_tool(module_path: Path) -> None:
    """Core runtime and domain code must not depend on the product investigation tool."""
    source = module_path.read_text(encoding="utf-8")
    imports = _imported_modules(source)
    leaks = {
        imp
        for imp in imports
        if imp == "tools.investigation" or imp.startswith("tools.investigation.")
    }
    assert not leaks, f"{module_path} imports product capability module(s): {sorted(leaks)}"
