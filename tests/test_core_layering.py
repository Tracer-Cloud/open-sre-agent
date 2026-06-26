"""Layering boundary test: core-facing packages must not import from ``cli``.

Core (``core/domain/``, ``core/orchestration/``) reports progress, prints debug
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
    Path("core/orchestration"),
    Path("platform/observability"),
)
# Anything imported from a forbidden prefix by a core module is a
# layering violation. Inverted dependency: core defines ports, CLI /
# vendor service packages implement them at the boundary.
#
# Forbidden prefixes:
# - ``cli`` — closed by #35 (observability ports). Core never
#   needs CLI internals; if you think you do, file a new
#   observability port instead.
# - ``services.tracer_client`` — closed by #36
#   (``integrations.port`` ``fetch_remote_integrations``). Other
#   ``services.*`` modules (LLM client, chat SDK adapter,
#   ``agent_llm_client``) stay allowed because they are core
#   capability access, not vendor-coupled like ``tracer_client``.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "cli",
    "services.tracer_client",
)


def _core_modules() -> list[Path]:
    files: list[Path] = []
    for root in _CORE_PACKAGES:
        files.extend(p for p in root.glob("**/*.py") if "__pycache__" not in p.parts)
    return sorted(files)


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
