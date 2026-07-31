"""Cloud backends for remote sync.

AWS/S3 and GCS are the built-in backends today — loaded lazily via the registry
(``provider=aws`` / ``provider=gcs``). Other clouds (Azure, …) are community
modules that call :func:`register_object_store`; the sync engine, CLI, and REPL
do not change.
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
