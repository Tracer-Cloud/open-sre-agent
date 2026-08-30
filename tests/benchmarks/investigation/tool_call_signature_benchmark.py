"""Compare the investigation tool-call cache identity paths.

Run locally:

    uv run python -m tests.benchmarks.investigation.tool_call_signature_benchmark

This benchmark intentionally has no timing assertion: it records the evidence
for a performance-sensitive refactor without making CI depend on host speed.
"""

from __future__ import annotations

import json
import statistics
import timeit
from collections.abc import Callable
from typing import Any

from core.llm.types import ToolCall
from tools.investigation.stages.gather_evidence.loop import tool_call_signature

_ITERATIONS = 50_000
_REPEATS = 7
_RESULT = {"traces": 12}

# Mirrors the nested arguments accepted by integrations.tempo.tools.query_tempo.
_TOOL_CALL = ToolCall(
    id="benchmark",
    name="query_tempo",
    input={
        "action": "search",
        "service": "checkout-api",
        "span_name": "POST /checkout",
        "min_duration_ms": 500.0,
        "max_duration_ms": 5_000.0,
        "tags": {
            "resource.cluster": "prod-eu",
            "http.status_code": "500",
        },
        "time_range_minutes": 60,
        "limit": 20,
    },
)


def _legacy_signature(tool_call: ToolCall) -> str:
    """Return the pre-refactor JSON identity for comparison only."""
    try:
        args = json.dumps(tool_call.input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(tool_call.input)
    return f"{tool_call.name}::{args}"


def _legacy_fresh_path() -> None:
    cache: dict[str, Any] = {}
    cache.get(_legacy_signature(_TOOL_CALL))
    cache.setdefault(_legacy_signature(_TOOL_CALL), _RESULT)


def _fingerprint_fresh_path() -> None:
    cache: dict[tuple[str, Any], Any] = {}
    signature = tool_call_signature(_TOOL_CALL)
    cache.get(signature)
    cache.setdefault(signature, _RESULT)


_LEGACY_DUPLICATE_CACHE = {_legacy_signature(_TOOL_CALL): _RESULT}
_FINGERPRINT_DUPLICATE_CACHE = {tool_call_signature(_TOOL_CALL): _RESULT}


def _legacy_duplicate_lookup() -> None:
    _LEGACY_DUPLICATE_CACHE.get(_legacy_signature(_TOOL_CALL))


def _fingerprint_duplicate_lookup() -> None:
    _FINGERPRINT_DUPLICATE_CACHE.get(tool_call_signature(_TOOL_CALL))


def _median_us(fn: Callable[[], object]) -> float:
    for _ in range(1_000):
        fn()
    samples = timeit.repeat(fn, number=_ITERATIONS, repeat=_REPEATS)
    return statistics.median(samples) * 1_000_000 / _ITERATIONS


def _report(label: str, legacy: Callable[[], object], fingerprint: Callable[[], object]) -> None:
    legacy_us = _median_us(legacy)
    fingerprint_us = _median_us(fingerprint)
    ratio = legacy_us / fingerprint_us
    print(
        f"{label}: legacy={legacy_us:.3f} us  fingerprint={fingerprint_us:.3f} us  "
        f"speedup={ratio:.2f}x"
    )


def main() -> None:
    _report("fresh lookup + store", _legacy_fresh_path, _fingerprint_fresh_path)
    _report("duplicate lookup", _legacy_duplicate_lookup, _fingerprint_duplicate_lookup)


if __name__ == "__main__":
    main()
