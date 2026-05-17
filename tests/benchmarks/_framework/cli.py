"""Standalone CLI for the benchmark framework.

Invoke from the opensre repo root with:

    uv run python -m tests.benchmarks._framework.cli <command> [args]

Subcommands:

    list                        Show available adapters and their metric schemas
    validate <config.yml>       Load + lint a config; exit non-zero if dishonest
    run <config.yml> [--dev]    Load config, instantiate adapter, run benchmark
    run-stub <config.yml>       Same as run but uses a fake LLM (no API cost)
                                — useful for testing the wiring

The CLI is deliberately standalone — not a subcommand of opensre's main CLI —
so the framework stays decoupled from opensre's CLI dispatcher. A future
``opensre bench`` subcommand can wrap this if user-facing surfacing is needed.

Exit codes:
    0   success
    1   config lint failed (anti-pattern)
    2   integrity gate blocked the run / report
    3   cost budget exceeded mid-run
    4   no adapter for ``config.benchmark``
    5   pre-flight failed for some other reason
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.benchmarks._framework.adapters import BenchmarkAdapter
from tests.benchmarks._framework.config import (
    load_config,
    validate_config_or_raise,
)
from tests.benchmarks._framework.cost import CostBudgetExceeded
from tests.benchmarks._framework.integrity import IntegrityViolation
from tests.benchmarks._framework.runner import BenchmarkRunner

# --------------------------------------------------------------------------- #
# Adapter registry                                                            #
# --------------------------------------------------------------------------- #


def _build_adapter(name: str) -> BenchmarkAdapter:
    """Map ``config.benchmark`` to an adapter instance.

    Registered adapters live in their own modules; the registry is here
    so the framework doesn't depend on any specific adapter.
    """
    if name == "cloudopsbench":
        # Late import — keeps the framework importable even if the adapter
        # has unmet deps (e.g., HF dataset not downloaded yet).
        from tests.benchmarks.cloudopsbench.adapter import CloudOpsBenchAdapter

        return CloudOpsBenchAdapter()
    raise KeyError(name)


def _known_adapters() -> list[str]:
    """Adapters this CLI knows how to construct. Keep in sync with ``_build_adapter``."""
    return ["cloudopsbench"]


# --------------------------------------------------------------------------- #
# Subcommands                                                                 #
# --------------------------------------------------------------------------- #


def _cmd_list(_args: argparse.Namespace) -> int:
    print("Adapters known to this CLI:")
    for name in _known_adapters():
        try:
            adapter = _build_adapter(name)
        except Exception as exc:
            print(f"  - {name}  (failed to construct: {exc})")
            continue
        schema = adapter.metric_schema()
        completeness = schema.validate_completeness()
        status = "✓ ready" if not completeness else f"⚠ {len(completeness)} issue(s)"
        print(f"  - {name} v{adapter.version}  ({len(schema.all_metrics())} metrics, {status})")
        if completeness:
            for err in completeness:
                print(f"      - {err}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if not path.exists():
        print(f"  ✗ {path} does not exist", file=sys.stderr)
        return 1
    try:
        config = validate_config_or_raise(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"  ✗ {path}\n{exc}", file=sys.stderr)
        return 1
    print(f"  ✓ {path}")
    print(f"      benchmark: {config.benchmark}")
    print(f"      modes: {config.modes}")
    print(f"      llms ({len(config.llms)}): {config.llms}")
    print(f"      runs_per_case: {config.runs_per_case}")
    print(f"      workers: {config.workers}")
    print(f"      cost_budget_usd: ${config.cost_budget_usd:.2f}")
    print(f"      output_dir: {config.output_dir}")
    if config.pre_registration_path:
        print(f"      pre_registration_path: {config.pre_registration_path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.config)
    try:
        config = load_config(path)
    except FileNotFoundError as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    if not args.dev:
        # Production runs MUST pass the lint pre-check
        lint_errors = config.lint()
        if lint_errors:
            print("  ✗ Config failed integrity lint:", file=sys.stderr)
            for err in lint_errors:
                print(f"    - {err}", file=sys.stderr)
            return 1

    try:
        adapter = _build_adapter(config.benchmark)
    except KeyError:
        print(
            f"  ✗ no adapter registered for benchmark={config.benchmark!r}. "
            f"Known: {_known_adapters()}",
            file=sys.stderr,
        )
        return 4

    runner = BenchmarkRunner(config=config, adapter=adapter)

    try:
        outcome = runner.run_without_integrity() if args.dev else runner.run()
    except IntegrityViolation as v:
        print(f"  ✗ Integrity gate blocked the run:\n{v}", file=sys.stderr)
        return 2
    except CostBudgetExceeded as exc:
        print(f"  ✗ Cost budget exceeded mid-run: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"  ✗ Pre-flight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5

    print()
    print(f"  ✓ Run complete: {len(outcome.cells)} cell(s), aborted={outcome.aborted}")
    print(f"  ✓ run_id: {outcome.report.run_id}")
    print(f"  ✓ artifacts: {outcome.report.raw_artifacts_dir}")
    if outcome.abort_reason:
        print(f"  ⚠ abort reason: {outcome.abort_reason}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Standalone CLI for the opensre benchmark framework.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="command")

    p_list = sub.add_parser("list", help="Show available adapters.")
    p_list.set_defaults(func=_cmd_list)

    p_validate = sub.add_parser("validate", help="Load + lint a config; exit non-zero on failure.")
    p_validate.add_argument("config", help="Path to YAML config.")
    p_validate.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="Run a benchmark from a YAML config.")
    p_run.add_argument("config", help="Path to YAML config.")
    p_run.add_argument(
        "--dev",
        action="store_true",
        help=(
            "DEVELOPMENT ONLY: skip integrity gates. Results stamped with "
            "dev_mode=True (run_id prefix) so they can't be silently promoted."
        ),
    )
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
