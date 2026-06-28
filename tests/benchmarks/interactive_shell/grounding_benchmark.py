"""Benchmark: interactive shell grounding (CLI + docs) cold vs warm cache.

Run locally:

    python -m tests.benchmarks.interactive_shell.grounding_benchmark
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from interactive_shell.agent_shell.grounding.cli_reference import CliReference
from interactive_shell.agent_shell.grounding.docs_reference import DocsReference


def _timed(label: str, fn: Callable[[], object]) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    print(f"{label}: {elapsed * 1000:.2f} ms")
    return elapsed, result


def main() -> None:
    docs_root = Path(__file__).resolve().parents[3] / "docs"

    cli = CliReference()
    docs = DocsReference()

    cold_cli, _ = _timed("CLI reference (cold)", cli.build_text)
    warm_cli, _ = _timed("CLI reference (warm)", cli.build_text)
    cli_stats = cli.stats()

    cold_docs = 0.0
    warm_docs = 0.0
    if docs_root.is_dir():
        cold_docs, _ = _timed("Docs parse (cold)", lambda: docs.discover(docs_root))
        warm_docs, _ = _timed("Docs parse (warm)", lambda: docs.discover(docs_root))
        _timed(
            "Docs reference text (warm index)",
            lambda: docs.build_text("configure Datadog integration"),
        )
    else:
        print("[skip] docs/ not present — docs parse timings omitted")

    docs_stats = docs.stats()

    print(
        f"\nSummary: CLI speedup ~{cold_cli / warm_cli:.1f}x (warm vs cold reference build)"
        if warm_cli > 0
        else "\nSummary: CLI warm path too fast to ratio"
    )
    if docs_root.is_dir() and warm_docs > 0:
        print(f"Docs parse speedup ~{cold_docs / warm_docs:.1f}x")
    print(f"CLI cache stats: {cli_stats}")
    print(f"Docs cache stats: {docs_stats}")


if __name__ == "__main__":
    main()
