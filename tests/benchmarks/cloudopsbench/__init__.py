"""Cloud-OpsBench synthetic benchmark integration.

Importing this package side-effects the opensre tool registry: the
CloudOpsBench-specific replay tools (under ``tools.k8s``) are registered
via :func:`app.tools.registry.register_external_tool_package` so the
agent loop sees them whenever a bench cell runs. Production code that
never imports ``tests.benchmarks.cloudopsbench`` never sees these tools
— the registry stays clean.

Keeping this side-effect at the package's ``__init__`` (rather than on
the first method call) ensures the registration happens before any
:func:`get_registered_tools` consumer asks for the registry snapshot.
"""

from __future__ import annotations

from app.tools.registry import register_external_tool_package
from tests.benchmarks.cloudopsbench import tools as _bench_tools_package

register_external_tool_package(_bench_tools_package)
