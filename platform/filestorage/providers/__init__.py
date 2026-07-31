"""Cloud backends for remote sync.

Built-in backends (``aws``, ``vercel``) load lazily via the registry when
selected. Other clouds (GCS, Azure, …) are community modules that call
:func:`register_object_store`; the sync engine, CLI, and REPL do not change.
"""

from __future__ import annotations

from platform.filestorage.providers.registry import (
    build_object_store,
    register_object_store,
    registered_providers,
    unregister_object_store,
)

__all__ = [
    "build_object_store",
    "register_object_store",
    "registered_providers",
    "unregister_object_store",
]
