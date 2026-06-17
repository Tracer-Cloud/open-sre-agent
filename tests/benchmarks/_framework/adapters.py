"""Backward-compatibility shim: re-exports the framework's adapter surface.

The original monolithic ``adapters.py`` mixed three responsibilities — data
contracts, the abstract adapter base, and the registry. Each now lives in
its own module under ``_framework/``:

  - ``types.py`` — ``CaseFilters``, ``BenchmarkCase``, ``AlertPayload``,
    ``RunResult``, ``CaseScore``, ``RunContext``, ``MetricSchema``, ``Mode``
  - ``adapter_base.py`` — the ``BenchmarkAdapter`` ABC + the
    ``apply_config_overrides`` strategy hook
  - ``registry.py`` — ``register_adapter``, ``build_adapter``,
    ``known_adapters``, ``ensure_known_adapters_registered``

Existing ``from tests.benchmarks._framework.adapters import X`` callers
continue to work — every public name from the three modules above is
re-exported here. New code should import from the focused modules
directly.
"""

from __future__ import annotations

from tests.benchmarks._framework.adapter_base import (
    AdapterCapabilities,
    BenchmarkAdapter,
    OverfitDimensions,
)
from tests.benchmarks._framework.registry import (
    build_adapter,
    capabilities_for,
    ensure_known_adapters_registered,
    known_adapters,
    register_adapter,
)
from tests.benchmarks._framework.types import (
    AlertPayload,
    BenchmarkCase,
    CaseFilters,
    CaseScore,
    MetricSchema,
    Mode,
    RunContext,
    RunResult,
)

__all__ = [
    "AdapterCapabilities",
    "AlertPayload",
    "BenchmarkAdapter",
    "BenchmarkCase",
    "CaseFilters",
    "CaseScore",
    "MetricSchema",
    "Mode",
    "OverfitDimensions",
    "RunContext",
    "RunResult",
    "build_adapter",
    "capabilities_for",
    "ensure_known_adapters_registered",
    "known_adapters",
    "register_adapter",
]
