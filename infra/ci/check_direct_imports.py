"""Enforce forbidden *direct* import edges between top-level packages.

Unlike import-linter (which flags transitive chains), this checker only
looks at top-level ``import`` / ``from … import`` statements — the same
AST walk as :mod:`infra.ci.check_import_cycles`. That makes it practical to
enforce layering incrementally: fix a direct edge, keep the contract.

Used by ``make check-imports`` (and :mod:`infra.ci.check_imports`) alongside
import-linter's config contract.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_import_cycles import _build_graph, discover_first_party_roots  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ``source_prefix -> forbidden destination roots`` for direct imports only.
# Enforces the layering contract documented in ``surfaces/__init__.py``:
# "Nothing first-party may import from surfaces/". Adds an explicit bound
# on ``platform``, ``infra``, ``core``, ``gateway`` so the surfaces ban
# is CI-enforced, not just doc-described.
#
# Note: ``infra`` is excluded from the import-graph's first-party roots
# (see ``check_import_cycles._SKIP_ROOT_DIRS``), so its rule here is
# defensive — it activates the moment ``infra/`` is brought into the
# graph. Keep it documented so the intent is not lost.
_FORBIDDEN_DIRECT: dict[str, frozenset[str]] = {
    "platform": frozenset({"surfaces"}),
    "infra": frozenset({"surfaces"}),
    "core": frozenset({"surfaces"}),
    "gateway": frozenset({"surfaces"}),
    "integrations": frozenset({"tools", "surfaces"}),
    "tools": frozenset({"surfaces"}),
}

# Known direct violations being burned down — remove entries as fixes land.
# Format: ``"source.module -> dest.module"`` (exact modules from the graph).
_BASELINE_IGNORES: frozenset[str] = frozenset(
    {
        # Gateway hosts the interactive_shell runtime — pre-existing reuse
        # to be burned down by extracting shared runtime primitives out of
        # ``surfaces/interactive_shell/`` and into a layer below ``surfaces``.
        "gateway.storage.session.resolver -> surfaces.interactive_shell.runtime.context",
        # tools/interactive_shell action tools reach UP into surfaces/interactive_shell
        # for runtime / command_registry / UI primitives. Clears when the action
        # tools themselves are refactored to be UI-agnostic (e.g. return
        # "approval-required" sentinels instead of calling execution_confirm
        # directly) so the surface owns its own confirmation UX.
        "tools.interactive_shell.actions.cli_command -> surfaces.interactive_shell.runtime.subprocess_runner",
        "tools.interactive_shell.actions.investigation -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.llm_provider -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.llm_provider -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.actions.sample_alert -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.command_registry.slash_catalog",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.utils.telemetry.turn_outcome",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.ui.foreground_investigation",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
    }
)


@dataclass(frozen=True)
class DirectViolation:
    source: str
    target: str

    @property
    def edge(self) -> str:
        return f"{self.source} -> {self.target}"


def _source_root(module: str) -> str:
    return module.split(".", 1)[0]


def find_direct_violations(
    graph: dict[str, set[str]],
    *,
    forbidden: dict[str, frozenset[str]] | None = None,
    baseline_ignores: frozenset[str] | None = None,
) -> list[DirectViolation]:
    rules = forbidden or _FORBIDDEN_DIRECT
    ignores = baseline_ignores if baseline_ignores is not None else _BASELINE_IGNORES
    violations: list[DirectViolation] = []

    for source_module, targets in sorted(graph.items()):
        source_root = _source_root(source_module)
        forbidden_roots = rules.get(source_root)
        if not forbidden_roots:
            continue
        for target_module in sorted(targets):
            target_root = _source_root(target_module)
            if target_root not in forbidden_roots:
                continue
            edge = DirectViolation(source_module, target_module)
            if edge.edge in ignores:
                continue
            violations.append(edge)
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    root = _REPO_ROOT
    first_party_roots = discover_first_party_roots(root)
    graph = _build_graph(root, first_party_roots)
    violations = find_direct_violations(graph)

    if not violations:
        print(
            "No forbidden direct import edges found "
            f"(baseline ignores {len(_BASELINE_IGNORES)} known edges)."
        )
        return 0

    print(f"FAIL: {len(violations)} forbidden direct import edge(s):")
    for violation in violations:
        print(f"  {violation.edge}")
    print(
        "\nFix by moving shared code to a lower layer (platform/common, core/contracts) "
        "or add a temporary baseline entry in infra/ci/check_direct_imports.py "
        "with a linked issue — do not use function-level lazy imports to hide "
        "new direct edges."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
