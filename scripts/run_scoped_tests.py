#!/usr/bin/env python3
"""Run only the test suites relevant to files changed on this branch.

Reads the changed file list from git, maps each path to its test target(s)
using the same rules as CI.md Section 2, and runs the minimal pytest
invocation that covers those targets. Falls back to ``make test-cov`` when
escalation rules trigger (shared/core code touched, or 3+ app areas changed).

Usage
-----
    make test-scope               # normal use
    make test-scope ARGS=--dry-run  # print command without running
    python scripts/run_scoped_tests.py [--dry-run] [--base <ref>]

Exit codes mirror pytest: 0 = all pass, non-zero = failure or config error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path → test-target mapping (mirrors CI.md Section 2, in priority order).
# Each entry: (path_prefix, test_targets, always_escalate)
#   path_prefix     – matched against the start of each changed file path
#   test_targets    – list of pytest paths/globs to run
#   always_escalate – True means touching this path alone forces make test-cov
# ---------------------------------------------------------------------------
_RULES: list[tuple[str, list[str], bool]] = [
    # Always-escalate paths (shared core)
    ("app/pipeline/", [], True),
    ("app/nodes/", [], True),
    ("app/types/", [], True),
    ("app/state/", [], True),
    ("app/utils/", [], True),
    # Specific sub-packages before their parent
    ("app/integrations/llm_cli/", ["tests/integrations/llm_cli/"], False),
    ("app/integrations/opensre/", ["tests/integrations/opensre/"], False),
    ("app/integrations/", ["tests/integrations/"], False),
    ("app/agent/", ["tests/agent/", "tests/agents/"], False),
    ("app/agents/", ["tests/agent/", "tests/agents/"], False),
    ("app/cli/", ["tests/cli/"], False),
    ("app/tools/", ["tests/tools/"], False),
    ("app/services/", ["tests/services/", "tests/tools/"], False),
    ("app/analytics/", ["tests/analytics/"], False),
    ("app/guardrails/", ["tests/test_guardrails/"], False),
    ("app/masking/", ["tests/masking/"], False),
    ("app/entrypoints/", ["tests/entrypoints/"], False),
    ("app/remote/", ["tests/remote/"], False),
    ("app/sandbox/", ["tests/sandbox/"], False),
    ("app/deployment/", ["tests/deployment/", "tests/app/deployment/"], False),
    ("app/delivery/", ["tests/delivery/"], False),
    ("app/auth/", ["tests/app/auth/"], False),
    ("app/hermes/", ["tests/hermes/"], False),
    ("app/watch_dog/", ["tests/watch_dog/"], False),
    ("app/webapp.py", ["tests/test_webapp.py"], False),
    # Config files that affect everything
    ("pyproject.toml", [], True),
    ("uv.lock", [], True),
    ("pytest.ini", [], True),
    ("Makefile", [], True),
    ("scripts/", [], True),
]

# Number of distinct app areas that triggers escalation to full test-cov.
_ESCALATION_AREA_THRESHOLD = 3


def _git_changed_files(base: str) -> list[str]:
    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", "HEAD", base],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        # Fall back to comparing against HEAD~1 if merge-base fails.
        merge_base = "HEAD~1"
    result = subprocess.check_output(
        ["git", "diff", "--name-only", merge_base],
        text=True,
    )
    return [f.strip() for f in result.splitlines() if f.strip()]


def _classify(changed: list[str]) -> tuple[bool, list[str], list[str]]:
    """Return (should_escalate, test_targets, matched_areas).

    matched_areas is used only for the escalation-threshold check.
    """
    escalate = False
    targets: list[str] = []
    areas: list[str] = []

    for path in changed:
        matched = False
        for prefix, test_paths, always_escalate in _RULES:
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                matched = True
                if always_escalate:
                    escalate = True
                else:
                    area = prefix.split("/")[1] if "/" in prefix else prefix
                    if area not in areas:
                        areas.append(area)
                    for t in test_paths:
                        if t not in targets:
                            targets.append(t)
                break

        if not matched:
            if path.startswith("tests/"):
                # Changed test file with no app counterpart — run it directly.
                if path not in targets:
                    targets.append(path)
            elif path.startswith("app/"):
                # app/ path with no explicit rule → escalate.
                escalate = True

    if len(areas) >= _ESCALATION_AREA_THRESHOLD:
        escalate = True

    # Filter targets to paths that actually exist (avoids pytest collection errors
    # when a directory was added in CI.md but hasn't been created yet).
    existing = [t for t in targets if Path(t).exists()]
    dropped = [t for t in targets if t not in existing]
    if dropped:
        print(f"  (skipping non-existent targets: {', '.join(dropped)})", flush=True)
    return escalate, existing, areas


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
    for f in changed:
        print(f"  {f}")

    escalate, targets, areas = _classify(changed)

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
