"""OpenSRE platform runtime services.

This package intentionally shares its name with Python's standard-library
``platform`` module. To keep third-party imports such as ``platform.system()``
working, the stdlib module's public attributes are loaded into this package
before OpenSRE subpackages are exposed.
"""

from __future__ import annotations

import importlib.util
import sysconfig
from pathlib import Path

_stdlib_platform_path = Path(sysconfig.get_path("stdlib")) / "platform.py"
_stdlib_platform_spec = importlib.util.spec_from_file_location(
    "_opensre_stdlib_platform",
    _stdlib_platform_path,
)

if _stdlib_platform_spec is not None and _stdlib_platform_spec.loader is not None:
    _stdlib_platform = importlib.util.module_from_spec(_stdlib_platform_spec)
    _stdlib_platform_spec.loader.exec_module(_stdlib_platform)

    for _name, _value in vars(_stdlib_platform).items():
        if _name.startswith("__") and _name not in {"__version__"}:
            continue
        globals().setdefault(_name, _value)

del Path
del importlib
del sysconfig

