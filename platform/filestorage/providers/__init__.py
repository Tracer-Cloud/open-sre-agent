"""Cloud backends for remote sync.

Built-in backends (``aws``, ``gcs``, ``vercel``) load lazily via the registry
when selected. Other clouds (Azure, …) are community modules that call
:func:`register_object_store`; the sync engine, CLI, and REPL do not change.
"""

from __future__ import annotations

from platform.filestorage.providers.registry import (
    SetupExtraField,
    build_object_store,
    credential_hint_for_provider,
    provider_extra_fields,
    register_object_store,
    registered_providers,
    unregister_object_store,
)

__all__ = [
    "SetupExtraField",
    "build_object_store",
    "credential_hint_for_provider",
    "provider_extra_fields",
    "register_object_store",
    "registered_providers",
    "unregister_object_store",
]
