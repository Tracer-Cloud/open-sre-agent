#!/usr/bin/env python3
"""Run pytest targets relevant to files changed on this branch.

Usage
-----
    make test-scope
    make test-scope ARGS=--dry-run
    python .github/ci/run_test_scope.py [--dry-run] [--base <ref>]

Exit codes mirror pytest: 0 = all pass, non-zero = failure or config error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from test_scope_rules import classify  # noqa: E402


def _commit_ref_exists(ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _resolve_base_ref(base: str) -> str:
    """Prefer branch refs for unqualified bases so same-named tags do not win."""
    if "/" in base or base.startswith("refs/"):
        return base
    for ref in (
        f"refs/heads/{base}",
        f"refs/remotes/origin/{base}",
        f"refs/remotes/upstream/{base}",
    ):
        if _commit_ref_exists(ref):
            return ref
    return base


def _git_changed_files(base: str) -> list[str]:
    resolved_base = _resolve_base_ref(base)
    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", "HEAD", resolved_base],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        merge_base = "HEAD~1"
    result = subprocess.check_output(
        ["git", "diff", "--name-only", merge_base],
        text=True,
    )
    return [f.strip() for f in result.splitlines() if f.strip()]


def _run(cmd: list[str], *, dry_run: bool) -> int:
    print(f"\n  $ {' '.join(cmd)}\n", flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print command without running.")
    parser.add_argument(
        "--base",
        default="main",
        help="Ref to diff against (default: main). Falls back to HEAD~1 if unavailable.",
    )
    args = parser.parse_args(argv)

    try:
        changed = _git_changed_files(args.base)
    except subprocess.CalledProcessError as exc:
        print(f"error: could not determine changed files: {exc}", file=sys.stderr)
        return 1

    if not changed:
        print("No changed files detected — nothing to test.")
        return 0

    print(f"Changed files ({len(changed)}):")
    for path in changed:
        print(f"  {path}")

    escalate, targets, areas = classify(changed)

    if escalate:
        print("\nEscalating to full unit suite (core/shared code or 3+ areas touched).")
        return _run(["make", "test-cov"], dry_run=args.dry_run)

    if not targets:
        print("\nNo test targets matched — running full unit suite as fallback.")
        return _run(["make", "test-cov"], dry_run=args.dry_run)

    print(f"\nAreas touched: {', '.join(areas)}")
    print(f"Running scoped tests: {' '.join(targets)}")
    return _run(
        [sys.executable, "-m", "pytest", *targets, "-v"],
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
