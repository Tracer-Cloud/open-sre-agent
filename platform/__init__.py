"""OpenSRE platform runtime services.

This package intentionally shares its name with Python's stdlib ``platform`` module.
Expose the stdlib module's public API here as well so existing ``import platform``
callers continue to behave as expected while project code can import subpackages
such as ``platform.analytics``.
"""

from __future__ import annotations

import importlib.util
import sysconfig
from pathlib import Path

_STDLIB_PLATFORM_PATH = Path(sysconfig.get_path("stdlib")) / "platform.py"
_STDLIB_PLATFORM_SPEC = importlib.util.spec_from_file_location(
    "_opensre_stdlib_platform", _STDLIB_PLATFORM_PATH
)
if _STDLIB_PLATFORM_SPEC is None or _STDLIB_PLATFORM_SPEC.loader is None:
    raise ImportError(f"Unable to load stdlib platform module from {_STDLIB_PLATFORM_PATH}")

_stdlib_platform = importlib.util.module_from_spec(_STDLIB_PLATFORM_SPEC)
_STDLIB_PLATFORM_SPEC.loader.exec_module(_stdlib_platform)

for _name in dir(_stdlib_platform):
    if _name.startswith("__") and _name not in {"__all__", "__version__"}:
        continue
    globals()[_name] = getattr(_stdlib_platform, _name)

__all__ = tuple(name for name in dir(_stdlib_platform) if not name.startswith("_"))
