"""Layering boundary test: core packages must not import from ``app.cli``.

Core (``app/agent/``, ``app/pipeline/``, ``app/utils/``) reports progress,
prints debug output, and renders investigation headers/footers through
the ports defined in :mod:`app.observability`. Reaching into
``app.cli.*`` directly couples the agent/pipeline layer to the REPL's
specific renderer and breaks headless / non-TTY callers.

See issue #35 and the introduction of ``build_*_provider`` /
``set_*`` injection helpers in ``app/observability/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE_PACKAGES: tuple[Path, ...] = (
    Path("app/agent"),
    Path("app/pipeline"),
    Path("app/utils"),
)
# Anything imported from ``app.cli.*`` by a core module is a layering
# violation. Inverted dependency: core defines ports, CLI implements
# them. Exemption note: at the time of writing none of the core
# packages legitimately need CLI internals; if a real need ever comes
# up, prefer a new observability port over an exemption.
_FORBIDDEN_PREFIXES: tuple[str, ...] = ("app.cli",)


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
def test_core_module_does_not_import_cli(module_path: Path) -> None:
    """Every module under ``app/agent/``, ``app/pipeline/``, ``app/utils/``
    must avoid imports from ``app.cli.*``.

    If you need progress reporting, debug output, or display rendering
    from core, use the ports under :mod:`app.observability` and let the
    CLI layer register its concrete implementation at boundary via
    ``install_cli_observability_adapters``.
    """
    source = module_path.read_text(encoding="utf-8")
    imports = _imported_modules(source)
    leaks = {
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES)
    }
    assert not leaks, (
        f"{module_path} imports CLI module(s) {sorted(leaks)} — route through an "
        "observability port (``app.observability.progress`` / ``debug`` / "
        "``display`` / ``output_format``) instead."
    )
