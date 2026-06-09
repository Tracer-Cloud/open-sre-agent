"""Benchmark adapter registry.

Each adapter declares its name and a zero-arg factory. The framework
dispatches on ``config.benchmark`` via this registry rather than an
if/elif chain — adding a new benchmark becomes a single
``register_adapter()`` call at adapter-module load time instead of an
edit to the framework CLI.

Lazy registration is the right policy: each adapter module pulls in its
own transitive dependencies (HF dataset loaders, replay backends, etc.)
that the framework should NOT need at import time. Callers bootstrap the
registry just-in-time via ``ensure_known_adapters_registered()`` before
consulting it.
"""

from __future__ import annotations

from collections.abc import Callable

from tests.benchmarks._framework.adapter_base import BenchmarkAdapter

_ADAPTER_FACTORIES: dict[str, Callable[[], BenchmarkAdapter]] = {}


def register_adapter(name: str, factory: Callable[[], BenchmarkAdapter]) -> None:
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

    Raises ``KeyError`` if no adapter is registered for that name — callers
    surface this with a helpful message that lists ``known_adapters()``.
    """
    if name not in _ADAPTER_FACTORIES:
        raise KeyError(name)
    return _ADAPTER_FACTORIES[name]()


def known_adapters() -> list[str]:
    """Sorted list of registered adapter names (stable for CLI output)."""
    return sorted(_ADAPTER_FACTORIES)


def ensure_known_adapters_registered() -> None:
    """Bootstrap: import every known adapter so its module-level
    ``register_adapter()`` call runs.

    Lives here (rather than at framework import time) to keep the
    framework importable without the cloudopsbench adapter's transitive
    deps. Callers (the CLI's ``run``/``validate``/``list`` subcommands)
    invoke this exactly once at startup.

    Adding a new adapter means: (1) add the module path to the lazy
    import chain below, (2) have the new adapter's module call
    ``register_adapter(NAME, FactoryClass)`` at module load. No CLI edit
    needed.
    """
    if known_adapters():
        return  # already bootstrapped this process
    # Late imports — each module's __init__ side-effects its own
    # ``register_adapter()`` call (or the adapter module does, depending on
    # the package layout). Catching ImportError surfaces missing deps
    # as a clear "this adapter cannot load" rather than a framework crash.
    import contextlib
    import importlib

    for module_path in (
        "tests.benchmarks.cloudopsbench.adapter",
        # Add new adapter module paths here.
    ):
        with contextlib.suppress(ImportError):
            importlib.import_module(module_path)
