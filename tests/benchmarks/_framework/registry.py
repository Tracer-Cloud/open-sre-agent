"""Benchmark adapter registry.

Each adapter declares its name and a zero-arg factory. The framework
dispatches on ``config.benchmark`` via this registry rather than an
if/elif chain.

Adding a new benchmark adapter:
  (1) Adapter module calls ``register_adapter(NAME, FactoryClass)`` at
      module load time.
  (2) Add the module path to ``_KNOWN_ADAPTER_MODULES`` below so the
      framework knows to lazily import it on first bootstrap.

That second step is a deliberate trade-off: a pure entry-points discovery
scheme would remove it, but at this scale (one repo, single-digit
adapters) the hardcoded list is more visible and easier to grep.

Lazy registration is the right policy: each adapter module pulls in its
own transitive dependencies (HF dataset loaders, replay backends, etc.)
that the framework should NOT need at import time. Callers do NOT have to
remember to bootstrap — ``build_adapter`` and ``known_adapters`` do it
on first use via the ``_bootstrapped`` sentinel.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from tests.benchmarks._framework.adapter_base import BenchmarkAdapter

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], BenchmarkAdapter]

_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {}

# Has ``ensure_known_adapters_registered`` been called this process? Kept
# separate from ``bool(_ADAPTER_FACTORIES)`` because tests (and any future
# code path) can pre-register mock adapters; the canonical bootstrap must
# still run regardless of what's already in the dict.
#
# Stored as a single-key dict (not a bare ``bool``) so the flag can be
# mutated from within a function without the ``global`` keyword — module
# scope name *rebinding* needs ``global``, but in-place mutation of a
# mutable object reached via its existing name does not. Same registry
# mutation pattern as ``_ADAPTER_FACTORIES`` itself.
_REGISTRY_STATE: dict[str, bool] = {"bootstrapped": False}

# Adapter modules the framework imports on first bootstrap. Each is
# expected to call ``register_adapter(name, factory)`` at module load.
_KNOWN_ADAPTER_MODULES: tuple[str, ...] = (
    "tests.benchmarks.cloudopsbench.adapter",
    # Add new adapter module paths here.
)


def register_adapter(name: str, factory: AdapterFactory) -> None:
    """Register an adapter factory under its benchmark name.

    Idempotent: re-registering the same (name, factory) pair is a no-op;
    re-registering a different factory under an already-claimed name is
    refused so the registry never silently swaps adapters mid-run.
    """
    existing = _ADAPTER_FACTORIES.get(name)
    if existing is factory:
        return
    if existing is not None:
        raise ValueError(
            f"adapter name {name!r} is already registered to a different "
            f"factory; refusing to swap silently"
        )
    _ADAPTER_FACTORIES[name] = factory


def build_adapter(name: str) -> BenchmarkAdapter:
    """Instantiate the adapter registered under ``name``.

    Auto-bootstraps the canonical adapter modules on first use so callers
    don't need to remember ``ensure_known_adapters_registered()``.

    Raises ``KeyError`` with the list of known adapters when ``name`` is
    not registered — so a typo surfaces as "did you mean one of [...]"
    rather than a one-line ``KeyError: 'foo'`` with no hint.
    """
    ensure_known_adapters_registered()
    if name not in _ADAPTER_FACTORIES:
        raise KeyError(
            f"no adapter registered as {name!r}. "
            f"known adapters: {known_adapters() or '<none registered>'}"
        )
    return _ADAPTER_FACTORIES[name]()


def known_adapters() -> list[str]:
    """Sorted list of registered adapter names (stable for CLI output).

    Auto-bootstraps on first use so ``opensre bench list`` and similar
    commands see the canonical adapter set without an explicit bootstrap
    call.
    """
    ensure_known_adapters_registered()
    return sorted(_ADAPTER_FACTORIES)


def ensure_known_adapters_registered() -> None:
    """Bootstrap: import every adapter module so each module-level
    ``register_adapter()`` call runs.

    Idempotent via the ``_REGISTRY_STATE["bootstrapped"]`` sentinel. The
    sentinel is set BEFORE the import loop so a partial failure (one
    adapter ImportErrors, another succeeds) doesn't trigger a retry on
    the next call — bootstrap happens exactly once per process. Operators
    who fix a broken adapter module restart the process.

    ImportError is logged then suppressed so a single missing optional
    dependency doesn't crash the framework, while typos / refactor breakage
    still surface as a visible warning — silent suppression would leave the
    registry empty and force every downstream "unknown adapter" diagnosis
    to start from scratch.
    """
    if _REGISTRY_STATE["bootstrapped"]:
        return
    _REGISTRY_STATE["bootstrapped"] = True
    import importlib

    for module_path in _KNOWN_ADAPTER_MODULES:
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            logger.warning(
                "[registry] adapter module %r failed to import: %s "
                "(missing optional dep OR a real typo/refactor — check above)",
                module_path,
                exc,
            )
