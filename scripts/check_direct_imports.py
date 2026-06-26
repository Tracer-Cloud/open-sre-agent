"""Enforce forbidden *direct* import edges between top-level packages.

Unlike import-linter (which flags transitive chains), this checker only
looks at top-level ``import`` / ``from … import`` statements — the same
AST walk as :mod:`scripts.check_import_cycles`. That makes it practical to
enforce layering incrementally: fix a direct edge, keep the contract.

Used by ``make check-imports`` (and ``scripts/check_imports.py``) alongside
import-linter's config contract.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_import_cycles import _build_graph, discover_first_party_roots  # noqa: E402

# ``source_prefix -> forbidden destination roots`` for direct imports only.
# Enforce the edges fixed in this PR first; expand to core/config once the
# registry port lands (see opensre-architecture-guide § tool placement).
_FORBIDDEN_DIRECT: dict[str, frozenset[str]] = {
    "integrations": frozenset({"tools", "cli"}),
    "tools": frozenset({"cli"}),
}

# Known direct violations being burned down — remove entries as fixes land.
# Format: ``"source.module -> dest.module"`` (exact modules from the graph).
_BASELINE_IGNORES: frozenset[str] = frozenset(
    {
        # Hermes Telegram sink reuses watch-dog alarm dispatch (#1500 refactor).
        "integrations.hermes.sinks -> tools.watch_dog.alarms",
        # Watch-dog still shares CLI error types and task records.
        "tools.watch_dog.alarms -> cli.interactive_shell.error_handling.errors",
        "tools.watch_dog.monitor -> cli.interactive_shell.runtime.tasks",
        "tools.watch_dog.process_monitor -> cli.interactive_shell.error_handling.errors",
        "tools.watch_dog.runner -> cli.interactive_shell.error_handling.exit_codes",
        # Integration setup UX still reaches into the REPL for prompts/theme.
        "integrations.cli -> cli.interactive_shell.data_store.context",
        "integrations.cli -> cli.interactive_shell.ui.theme",
        "integrations.cli -> cli.wizard.integration_health",
        "integrations.__main__ -> cli.interactive_shell.ui.prompt_support",
        "integrations.github_mcp -> cli.interactive_shell.ui.theme",
        "integrations.vercel_incidents -> cli.interactive_shell.data_store.context",
        "integrations.vercel_incidents -> cli.investigation",
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
        "or add a temporary baseline entry in scripts/check_direct_imports.py "
        "with a linked issue — do not use function-level lazy imports to hide "
        "new direct edges."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
