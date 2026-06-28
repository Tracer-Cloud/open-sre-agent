"""Layering boundary tests for the orchestration pipeline runtime.

The pipeline orchestrator coordinates stages; it must not import from
transport-layer modules. Integration-specific wiring lives behind the
``tools.investigation.reporting.upstream_correlation`` factory (and
similar factories for future correlation sources).

Without this guard the dependency drift is easy:
``tools.investigation.lifecycle`` previously imported ``DatadogClient``
directly, coupling the orchestrator to one vendor and making "add a
second correlation source" an edit-this-file change instead of a
new-file change. See issue #34 and the refactor that introduced
``build_upstream_evidence_provider``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ORCHESTRATION_PIPELINE_FILES: tuple[Path, ...] = (
    Path("tools/investigation/lifecycle.py"),
    Path("tools/investigation/capability.py"),
    Path("tools/investigation/state_factory.py"),
    Path("tools/investigation/streaming.py"),
)
# ``infra.deployment.remote`` is a transport-layer package (HTTP client, SSE parser). The
# orchestration core must not depend on it — domain types it shares with
# the remote runner live in ``core.domain`` (see ``StreamEvent``).
# ``cli`` is the presentation layer; same rule.
_FORBIDDEN_PREFIXES: tuple[str, ...] = ("infra.deployment.remote", "cli")


def _orchestration_pipeline_modules() -> list[Path]:
    return sorted(path for path in _ORCHESTRATION_PIPELINE_FILES if path.exists())


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


@pytest.mark.parametrize("module_path", _orchestration_pipeline_modules(), ids=str)
def test_orchestration_pipeline_module_does_not_import_forbidden_layer(
    module_path: Path,
) -> None:
    """Every orchestration pipeline runtime module must avoid forbidden imports."""
    source = module_path.read_text(encoding="utf-8")
    imports = _imported_modules(source)
    leaks = {
        imp
        for imp in imports
        if any(imp == prefix or imp.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES)
    }
    assert not leaks, (
        f"{module_path} imports forbidden module(s) {sorted(leaks)} — route through "
        "an abstraction (e.g. "
        "``tools.investigation.reporting.upstream_correlation."
        "build_upstream_evidence_provider``) instead."
    )
