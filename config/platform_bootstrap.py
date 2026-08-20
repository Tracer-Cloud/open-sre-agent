"""Keep OpenSRE's ``platform`` package ahead of stdlib ``platform``.

The product package re-uses the stdlib name and re-exports its API. Hosts
(console scripts, uvicorn, …) can cache the stdlib **module** in
``sys.modules`` first; later ``from platform.analytics…`` then fails because
that module is not a package.

Call :func:`ensure_project_platform_package` once at each composition root
(process entry, ``configure_process``, ``run_repl*``, test conftests). It:

1. Loads the project package into ``sys.modules["platform"]`` if needed.
2. Installs a process-wide import hook so a later stdlib re-cache self-heals
   on the next ``platform`` / ``platform.*`` import.

Application modules import ``platform.*`` normally — no per-leaf ensure.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType

from config.constants.paths import REPO_ROOT

_ImportFn = Callable[..., ModuleType]
_ImportModuleFn = Callable[..., ModuleType]


@dataclass(slots=True)
class _ImportGuard:
    """Live import wrappers. ``beneath_*`` are what we call next (usually stock)."""

    beneath_import: _ImportFn
    beneath_import_module: _ImportModuleFn
    wrapped_import: _ImportFn
    wrapped_import_module: _ImportModuleFn


_guard: _ImportGuard | None = None

#: Serializes the rare eviction+reload so parallel gateway turns cannot heal a
#: re-cached ``platform`` against a half-populated ``sys.modules``.
_load_lock = threading.Lock()


def ensure_project_platform_package() -> None:
    """Make ``platform.*`` resolve to this repo; install the import guard once."""
    _ensure_project_platform_loaded()
    _install_import_guard()


def platform_import_guard_is_active() -> bool:
    """True when our wrappers are the current ``__import__`` / ``import_module``."""
    return (
        _guard is not None
        and builtins.__import__ is _guard.wrapped_import
        and importlib.import_module is _guard.wrapped_import_module
    )


def reset_platform_import_guard_for_tests() -> None:
    """Restore stock importers. Production never uninstalls the guard."""
    global _guard
    active = _guard
    if active is None:
        return
    if builtins.__import__ is active.wrapped_import:
        builtins.__import__ = active.beneath_import
    if importlib.import_module is active.wrapped_import_module:
        importlib.import_module = active.beneath_import_module  # type: ignore[method-assign]
    _guard = None


def _ensure_project_platform_loaded() -> None:
    if _project_platform_is_loaded():
        return

    with _load_lock:
        # Re-check under the lock: another thread may have healed while we waited.
        if _project_platform_is_loaded():
            return
        _evict_cached_platform_modules()
        _load_project_platform_into_sys_modules()


def _project_platform_is_loaded() -> bool:
    current = sys.modules.get("platform")
    return current is not None and hasattr(current, "__path__")


def _evict_cached_platform_modules() -> None:
    for name in list(sys.modules):
        if name == "platform" or name.startswith("platform."):
            del sys.modules[name]


def _load_project_platform_into_sys_modules() -> None:
    package_dir = REPO_ROOT / "platform"
    init_path = package_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "platform",
        init_path,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load OpenSRE platform package from {init_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["platform"] = module
    spec.loader.exec_module(module)


def _targets_project_platform(module_name: str) -> bool:
    return module_name == "platform" or module_name.startswith("platform.")


def _install_import_guard() -> None:
    """Hook ``__import__`` and ``import_module`` so a stdlib re-cache cannot stick.

    Wrappers close over the importer they call next. Re-installing never wraps
    our own wrapper (that would recurse).
    """
    global _guard

    if platform_import_guard_is_active():
        return

    beneath_import = builtins.__import__
    beneath_import_module = importlib.import_module
    if _guard is not None:
        # Stale state (flag lied): peel back to what our previous wrapper called.
        if beneath_import is _guard.wrapped_import:
            beneath_import = _guard.beneath_import
        if beneath_import_module is _guard.wrapped_import_module:
            beneath_import_module = _guard.beneath_import_module

    def wrapped_import(
        name: str,
        globals: Mapping[str, object] | None = None,  # noqa: A002 — builtins signature
        locals: Mapping[str, object] | None = None,  # noqa: A002
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if level == 0 and _targets_project_platform(name):
            _ensure_project_platform_loaded()
        return beneath_import(name, globals, locals, fromlist, level)

    def wrapped_import_module(name: str, package: str | None = None) -> ModuleType:
        absolute = (
            importlib.util.resolve_name(name, package)
            if package is not None and name.startswith(".")
            else name
        )
        if _targets_project_platform(absolute):
            _ensure_project_platform_loaded()
        return beneath_import_module(name, package)

    _guard = _ImportGuard(
        beneath_import=beneath_import,
        beneath_import_module=beneath_import_module,
        wrapped_import=wrapped_import,
        wrapped_import_module=wrapped_import_module,
    )
    builtins.__import__ = wrapped_import
    importlib.import_module = wrapped_import_module  # type: ignore[method-assign]
