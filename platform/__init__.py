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


def _load_stdlib_platform():
    """Load the stdlib ``platform`` module.

    PyInstaller patches ``sysconfig`` in frozen builds so
    ``sysconfig.get_path("stdlib")`` resolves correctly inside the bundled
    Python stdlib without any special handling here.
    """
    stdlib_dir = sysconfig.get_path("stdlib")
    if stdlib_dir is not None and (Path(stdlib_dir) / "platform.py").is_file():
        stdlib_path = Path(stdlib_dir) / "platform.py"
        spec = importlib.util.spec_from_file_location("_opensre_stdlib_platform", stdlib_path)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    raise ImportError(
        "Unable to load stdlib platform module — sysconfig path "
        f"{stdlib_dir!r} does not contain platform.py"
    )


_stdlib_platform = _load_stdlib_platform()

for _name in dir(_stdlib_platform):
    if _name.startswith("__") and _name not in {"__all__", "__version__"}:
        continue
    globals()[_name] = getattr(_stdlib_platform, _name)

__all__ = tuple(name for name in dir(_stdlib_platform) if not name.startswith("_"))
