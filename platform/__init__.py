"""OpenSRE platform runtime services.

This package intentionally shares its name with Python's stdlib ``platform`` module.
Expose the stdlib module's public API here as well so existing ``import platform``
callers continue to behave as expected while project code can import subpackages
such as ``platform.analytics``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import sysconfig
from pathlib import Path

# Directory (relative to ``sys._MEIPASS``) where the release build stages a copy
# of the genuine stdlib ``platform.py``. PyInstaller does not lay out the stdlib
# as loose ``.py`` files on disk, and this package shadows the ``platform`` name,
# so the frozen binary cannot otherwise reach the real module. The release
# workflow bundles it here; keep this constant in sync with that ``--add-data``
# destination.
_FROZEN_STDLIB_DIR = "_opensre_stdlib_platform"


def _candidate_stdlib_platform_paths() -> list[Path]:
    """Return the locations to probe for the genuine stdlib ``platform.py``."""
    candidates: list[Path] = []

    # Frozen builds (PyInstaller): a copy bundled into the extracted data tree.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / _FROZEN_STDLIB_DIR / "platform.py")

    # Regular source checkout / installed interpreter.
    stdlib_dir = sysconfig.get_path("stdlib")
    if stdlib_dir:
        candidates.append(Path(stdlib_dir) / "platform.py")

    # Last resort: sit next to another known stdlib module on disk.
    os_file = getattr(os, "__file__", None)
    if os_file:
        candidates.append(Path(os_file).resolve().parent / "platform.py")

    return candidates


def _load_stdlib_platform():
    """Load the genuine stdlib ``platform`` module.

    This package intentionally shadows the stdlib ``platform`` name, so the real
    module is loaded directly from its source file. In frozen builds the stdlib
    is not available as loose ``.py`` files, so the release build bundles a copy
    that we load from ``sys._MEIPASS`` (see ``_FROZEN_STDLIB_DIR``).
    """
    candidates = _candidate_stdlib_platform_paths()
    for stdlib_path in candidates:
        if not stdlib_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_opensre_stdlib_platform", stdlib_path)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    checked = ", ".join(repr(str(path)) for path in candidates) or "<no candidate paths>"
    raise ImportError(f"Unable to load stdlib platform module — checked: {checked}")


_stdlib_platform = _load_stdlib_platform()

for _name in dir(_stdlib_platform):
    if _name.startswith("__") and _name not in {"__all__", "__version__"}:
        continue
    globals()[_name] = getattr(_stdlib_platform, _name)

__all__ = tuple(name for name in dir(_stdlib_platform) if not name.startswith("_"))
