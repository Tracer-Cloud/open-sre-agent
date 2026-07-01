"""Run all import-graph quality checks in one command.

Orchestrates, in order:

1. **Cycles** — module-load SCCs (``check_import_cycles``)
2. **Layers** — ``config`` independence via import-linter (``.importlinter``)
3. **Direct edges** — forbidden top-level imports (``check_direct_imports``)

Used by ``make check-imports``, ``make check``, and CI.

Exit codes:
    0 — all checks passed
    1 — one or more checks failed (each section prints its own detail)
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import main as check_direct_imports  # noqa: E402
from check_import_cycles import main as check_import_cycles  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ImportCheck:
    name: str
    run: Callable[[], int]


def _run_importlinter(*, config: Path | None = None) -> int:
    lint_imports = Path(sys.executable).with_name("lint-imports")
    if not lint_imports.is_file():
        print(
            "lint-imports not found — install dev deps (import-linter package).",
            file=sys.stderr,
        )
        return 1
    command = [str(lint_imports)]
    if config is not None:
        command.extend(["--config", str(config)])
    completed = subprocess.run(command, cwd=_REPO_ROOT, check=False)
    return int(completed.returncode)


def import_checks(*, strict_layers: bool = False) -> Sequence[ImportCheck]:
    layer_config = _REPO_ROOT / ".importlinter.strict" if strict_layers else None
    return (
        ImportCheck("Import cycles (Tarjan SCC)", check_import_cycles),
        ImportCheck(
            "Import layers (import-linter)",
            lambda: _run_importlinter(config=layer_config),
        ),
        ImportCheck("Forbidden direct import edges", check_direct_imports),
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    strict_layers = False
    if args == ["--strict"]:
        strict_layers = True
        args = []
    if args:
        print(f"Unknown arguments: {' '.join(args)}", file=sys.stderr)
        print("Usage: check_imports.py [--strict]", file=sys.stderr)
        return 2

    checks = import_checks(strict_layers=strict_layers)
    failures: list[str] = []

    for index, check in enumerate(checks, start=1):
        print(f"=== [{index}/{len(checks)}] {check.name} ===")
        exit_code = check.run()
        if exit_code != 0:
            failures.append(check.name)
        print()

    if not failures:
        print(f"All {len(checks)} import checks passed.")
        return 0

    print(f"FAIL: {len(failures)} import check(s) failed:")
    for name in failures:
        print(f"  - {name}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
