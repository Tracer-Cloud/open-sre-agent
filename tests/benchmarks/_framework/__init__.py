"""Reusable benchmark framework for opensre.

Architecture: a benchmark framework with pluggable adapters per benchmark
suite. CloudOpsBench is the first adapter; OpenRCA and ToolCallBench follow.

See design: ``~/DevBox/opensre-notes/opensre-benchmark-framework.md``.

Modules:
    adapters    Abstract ``BenchmarkAdapter`` ABC + typed data contracts.
    config      YAML config loader + integrity-aware validation.
    (later)     runner, llm_dispatch, cost, scoring, reporting, integrity.
"""

from __future__ import annotations

__all__ = [
    "adapters",
    "config",
]
