"""Layering boundary tests for ``app/pipeline/``.

The pipeline orchestrator coordinates stages; it must not import from
vendor service modules. Vendor wiring lives behind the
``app.agent.correlation`` factory (and similar factories for future
correlation sources).

Without this guard the dependency drift is easy:
``app.pipeline.pipeline`` previously imported ``DatadogClient``
directly, coupling the orchestrator to one vendor and making "add a
second correlation source" an edit-this-file change instead of a
new-file change. See issue #34 and the refactor that introduced
``build_upstream_evidence_provider``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PIPELINE_DIR = Path("app/pipeline")
# Block *all* vendor service modules, not just Datadog. The whole pattern
# is that ``app/pipeline/`` routes vendor wiring through a factory in
# ``app/agent/correlation/`` (or analogous), so reaching directly into
# ``app.services.<anything>`` is a layering violation regardless of vendor.
# This guards against future Grafana/AWS/etc. imports without manual edits.
_FORBIDDEN_PREFIXES: tuple[str, ...] = ("app.services",)


def _pipeline_modules() -> list[Path]:
    return sorted(p for p in _PIPELINE_DIR.glob("**/*.py") if "__pycache__" not in p.parts)


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


@pytest.mark.parametrize("module_path", _pipeline_modules(), ids=str)
def test_pipeline_module_does_not_import_forbidden_layer(module_path: Path) -> None:
    """Every module under ``app/pipeline/`` must avoid forbidden vendor imports."""
    source = module_path.read_text(encoding="utf-8")
    imports = _imported_modules(source)
    leaks = {
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES)
    }
    assert not leaks, (
        f"{module_path} imports vendor service module(s) {sorted(leaks)} — route through "
        "an abstraction (e.g. ``app.agent.correlation.build_upstream_evidence_provider``) instead."
    )
