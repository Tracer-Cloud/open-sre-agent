"""Bootstrap the project ``platform`` package when stdlib ``platform`` loaded first."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def ensure_project_platform_package() -> None:
    """Ensure ``platform.<opensre module>`` imports resolve to this repository.

    Console-script launchers and some tooling import Python's stdlib ``platform``
    before importing OpenSRE. Once that module is cached in ``sys.modules``, later
    imports such as ``platform.analytics`` fail because the stdlib module is not a
    package. The local package proxies the stdlib API, so replacing the cache entry
    keeps both ``import platform`` and ``from platform.analytics`` working.
    """
    current = sys.modules.get("platform")
    if current is not None and hasattr(current, "__path__"):
        return

    repo_root = Path(__file__).resolve().parent.parent
    package_dir = repo_root / "platform"
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
