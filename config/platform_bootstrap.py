"""Bootstrap the project ``platform`` package when stdlib ``platform`` loaded first.

OpenSRE's first-party ``platform`` package intentionally shares the stdlib name
and re-exports its API. Console scripts, uvicorn, and other hosts can cache
stdlib ``platform`` (a module, not a package) in ``sys.modules`` before any
OpenSRE import. Later ``from platform.analytics…`` then fails.

Design (no per-leaf ensure sprawl):

1. **Heal** — :func:`ensure_project_platform_package` loads the project package
   into ``sys.modules["platform"]`` (idempotent).
2. **Guard** — the same call installs a process-wide import guard once. Any later
   ``import platform`` / ``from platform…`` / ``importlib.import_module("platform…")``
   re-heals if a host re-cached the stdlib module mid-process.

Call ``ensure_project_platform_package`` only at composition roots (process
entry, ``configure_process``, public ``run_repl*``, test confests). Application
modules import ``platform.*`` normally.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import sys
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any, Final

from config.constants.paths import REPO_ROOT

_GUARD_INSTALLED: bool = False
_ORIGINAL_IMPORT: Any = None
_ORIGINAL_IMPORT_MODULE: Any = None

# Marker on ``sys`` so tests can detect the guard without importing private flags.
_GUARD_SYS_ATTR: Final = "_opensre_platform_import_guard"


def ensure_project_platform_package() -> None:
    """Ensure ``platform.<opensre module>`` imports resolve to this repository.

    Idempotent heal of ``sys.modules["platform"]``, then install the process
    import guard once so later re-caches of stdlib ``platform`` self-correct on
    the next ``platform`` import.
    """
    _heal_project_platform_in_sys_modules()
    _install_platform_import_guard()


def _heal_project_platform_in_sys_modules() -> None:
    current = sys.modules.get("platform")
    if current is not None and hasattr(current, "__path__"):
        return

    for name in list(sys.modules):
        if name == "platform" or name.startswith("platform."):
            del sys.modules[name]

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


def _platform_import_name(name: str) -> bool:
    return name == "platform" or name.startswith("platform.")


def _install_platform_import_guard() -> None:
    """Wrap import so a mid-process stdlib re-cache cannot stick."""
    global _GUARD_INSTALLED, _ORIGINAL_IMPORT, _ORIGINAL_IMPORT_MODULE
    if _GUARD_INSTALLED:
        return

    _ORIGINAL_IMPORT = builtins.__import__
    _ORIGINAL_IMPORT_MODULE = importlib.import_module

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,  # noqa: A002 — matches builtins
        locals: Mapping[str, object] | None = None,  # noqa: A002
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        if level == 0 and _platform_import_name(name):
            _heal_project_platform_in_sys_modules()
        return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    def guarded_import_module(name: str, package: str | None = None) -> ModuleType:
        absolute = name
        if package is not None and name.startswith("."):
            absolute = importlib.util.resolve_name(name, package)
        if _platform_import_name(absolute):
            _heal_project_platform_in_sys_modules()
        return _ORIGINAL_IMPORT_MODULE(name, package)

    builtins.__import__ = guarded_import
    importlib.import_module = guarded_import_module  # type: ignore[method-assign]
    _GUARD_INSTALLED = True
    setattr(sys, _GUARD_SYS_ATTR, True)


def reset_platform_import_guard_for_tests() -> None:
    """Restore stock importers. Production never uninstalls the guard."""
    global _GUARD_INSTALLED, _ORIGINAL_IMPORT, _ORIGINAL_IMPORT_MODULE
    if not _GUARD_INSTALLED:
        return
    if _ORIGINAL_IMPORT is not None:
        builtins.__import__ = _ORIGINAL_IMPORT
    if _ORIGINAL_IMPORT_MODULE is not None:
        importlib.import_module = _ORIGINAL_IMPORT_MODULE  # type: ignore[method-assign]
    _ORIGINAL_IMPORT = None
    _ORIGINAL_IMPORT_MODULE = None
    _GUARD_INSTALLED = False
    if hasattr(sys, _GUARD_SYS_ATTR):
        delattr(sys, _GUARD_SYS_ATTR)
