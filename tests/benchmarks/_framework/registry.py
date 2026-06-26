"""Benchmark adapter registry.

Maps ``config.benchmark`` → adapter factory so the framework can build
adapters without an if/elif chain on the benchmark name.

Adding a new adapter:
  1. Create ``tests/benchmarks/<name>/adapter.py``.
  2. At module load, the file calls ``register_adapter(NAME, FactoryClass)``.

Discovery is automatic. Bootstrap walks ``tests/benchmarks/*/adapter.py``
on first registry use. Directories with names starting with ``_`` or
``.``, or without an ``adapter.py``, are skipped (``_framework/`` and
``interactive_shell/`` are the current examples).

Bootstrap is lazy because adapter modules pull in heavy transitive
deps (HF dataset loaders, replay backends). Importing them at framework
load time is wrong — only do it when a caller actually needs an
adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from tests.benchmarks._framework.adapter_base import AdapterCapabilities, BenchmarkAdapter

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], BenchmarkAdapter]

_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {}

# Bootstrap-once flag. Kept separate from ``bool(_ADAPTER_FACTORIES)``
# because tests can pre-register mocks; the canonical bootstrap must
# still run independent of dict contents. Stored in a dict (not a bare
# bool) so we can mutate it without ``global`` rebinding.
_REGISTRY_STATE: dict[str, bool] = {"bootstrapped": False}


def _discover_adapter_modules() -> tuple[str, ...]:
    """Walk ``tests/benchmarks/`` and return adapter module paths.

    Rules:
      - Immediate subdirectories only.
      - Skip names starting with ``_`` or ``.``.
      - Require an ``adapter.py`` inside the subdirectory.

    Output is sorted for deterministic bootstrap order.
    """
    benchmarks_dir = Path(__file__).resolve().parent.parent
    discovered: list[str] = []
    for child in sorted(benchmarks_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        if not (child / "adapter.py").is_file():
            continue
        discovered.append(f"tests.benchmarks.{child.name}.adapter")
    return tuple(discovered)


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


def capabilities_for(name: str) -> AdapterCapabilities:
    """Return the adapter's capability flags without instantiating it.

    When the registered factory is the adapter class itself (the common
    case: ``register_adapter("cloudopsbench", CloudOpsBenchAdapter)``),
    we read ``capabilities`` directly off the class — no ``__init__``
    runs, no side effects. Falls back to instantiating closure
    factories.

    Raises ``KeyError`` with the same "known adapters" hint as
    ``build_adapter`` for unregistered names.
    """
    ensure_known_adapters_registered()
    if name not in _ADAPTER_FACTORIES:
        raise KeyError(
            f"no adapter registered as {name!r}. "
            f"known adapters: {known_adapters() or '<none registered>'}"
        )
    factory = _ADAPTER_FACTORIES[name]
    if isinstance(factory, type) and issubclass(factory, BenchmarkAdapter):
        return factory.capabilities
    # Closure / lambda factory — fall back to instantiation. Uncommon
    # but supported; the registry accepts any zero-arg callable.
    return factory().capabilities


def known_adapters() -> list[str]:
    """Sorted list of registered adapter names (stable for CLI output).

    Auto-bootstraps on first use so ``opensre bench list`` and similar
    commands see the canonical adapter set without an explicit bootstrap
    call.
    """
    ensure_known_adapters_registered()
    return sorted(_ADAPTER_FACTORIES)


def ensure_known_adapters_registered() -> None:
    """Import every discovered adapter module so its
    ``register_adapter()`` call runs.

    Idempotent: runs once per process (guarded by
    ``_REGISTRY_STATE["bootstrapped"]``). The flag is set BEFORE the
    import loop so a single failing adapter doesn't trigger retries on
    subsequent calls. Fix the adapter and restart the process.

    ImportError is logged-and-suppressed so one missing optional dep
    doesn't crash the framework. The warning surfaces typos and
    refactor breakage; silent suppression would leave the registry
    empty and obscure the root cause.
    """
    if _REGISTRY_STATE["bootstrapped"]:
        return
    _REGISTRY_STATE["bootstrapped"] = True
    import importlib

    for module_path in _discover_adapter_modules():
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            logger.warning(
                "[registry] adapter module %r failed to import: %s "
                "(missing optional dep OR a real typo/refactor — check above)",
                module_path,
                exc,
            )
