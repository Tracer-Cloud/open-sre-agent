"""Benchmark: interactive shell grounding (CLI + docs) cold vs warm cache.

Run locally:

    python -m tests.benchmarks.interactive_shell.grounding_benchmark
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from app.cli.interactive_shell.cli_reference import (
    build_cli_reference_text,
    get_cli_reference_cache_stats,
    invalidate_cli_reference_cache,
)
from app.cli.interactive_shell.docs_reference import (
    build_docs_reference_text,
    discover_docs,
    get_docs_cache_stats,
    invalidate_docs_cache,
)


def _timed(label: str, fn: Callable[[], object]) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    print(f"{label}: {elapsed * 1000:.2f} ms")  # noqa: T201
    return elapsed, result


def main() -> None:
    docs_root = Path(__file__).resolve().parents[3] / "docs"

    invalidate_cli_reference_cache()
    invalidate_docs_cache()

    cold_cli, _ = _timed("CLI reference (cold)", build_cli_reference_text)
    warm_cli, _ = _timed("CLI reference (warm)", build_cli_reference_text)
    cli_stats = get_cli_reference_cache_stats()

    cold_docs = 0.0
    warm_docs = 0.0
    if docs_root.is_dir():
        cold_docs, _ = _timed("Docs parse (cold)", lambda: discover_docs(docs_root))
        warm_docs, _ = _timed("Docs parse (warm)", lambda: discover_docs(docs_root))
        _timed(
            "Docs reference text (warm index)",
            lambda: build_docs_reference_text("configure Datadog integration"),
        )
    else:
        print("[skip] docs/ not present — docs parse timings omitted")  # noqa: T201

    docs_stats = get_docs_cache_stats()

    print(  # noqa: T201
        f"\nSummary: CLI speedup ~{cold_cli / warm_cli:.1f}x (warm vs cold reference build)"
        if warm_cli > 0
        else "\nSummary: CLI warm path too fast to ratio"
    )
    if docs_root.is_dir() and warm_docs > 0:
        print(f"Docs parse speedup ~{cold_docs / warm_docs:.1f}x")  # noqa: T201
    print(f"CLI cache stats: {cli_stats}")  # noqa: T201
    print(f"Docs cache stats: {docs_stats}")  # noqa: T201


if __name__ == "__main__":
    main()
