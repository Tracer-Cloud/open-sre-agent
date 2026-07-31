"""Register object-store backends without changing the sync engine.

The engine talks only to :class:`~platform.filestorage.ports.ObjectStore`.
Each cloud vendor ships a factory here; surfaces call
:func:`build_object_store` and never import a vendor module directly.
Adding GCS, Azure, or another backend is a new module plus one
``register`` call — the engine, CLI, and REPL stay closed (open/closed
principle).

The registry dict is process-global (plugin table). Mutations and lookups are
serialized with an ``RLock`` so concurrent ``build_object_store`` calls from
the shared sync service stay safe. Factories run **outside** the lock.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from platform.filestorage.enums import BuiltInProvider
from platform.filestorage.errors import RemoteSyncConfigError

if TYPE_CHECKING:
    from platform.filestorage.config import RemoteSyncConfig
    from platform.filestorage.ports import ObjectStore

ObjectStoreFactory = Callable[["RemoteSyncConfig"], "ObjectStore"]

_DEFAULT_CREDENTIAL_HINT = "Use ambient credentials for this provider; opensre does not store them."

_REGISTRY: dict[str, ObjectStoreFactory] = {}
_CREDENTIAL_HINTS: dict[str, str] = {}
_REGISTRY_LOCK = threading.RLock()

# Built-in backends, imported on first use so this package never imports itself.
# Each module calls ``register_object_store`` at import time; a third party can
# register ahead of time instead and never appear here.
_BUILTIN_MODULES = {
    BuiltInProvider.AWS.value: "platform.filestorage.providers.aws",
    BuiltInProvider.VERCEL.value: "platform.filestorage.providers.vercel",
}


def _load_builtin(key: str) -> None:
    """Import a built-in module under the registry lock (caller holds it)."""
    module = _BUILTIN_MODULES.get(key)
    if module is not None and key not in _REGISTRY:
        importlib.import_module(module)


def register_object_store(
    name: str, factory: ObjectStoreFactory, *, credential_hint: str | None = None
) -> None:
    """Bind ``name`` (the value of ``OPENSRE_REMOTE_SYNC_PROVIDER``) to a factory.

    ``credential_hint`` is the one-line, secret-free sentence shown after
    ``setup`` about where this provider's ambient credentials come from (see
    :func:`credential_hint_for_provider`). Omit it to fall back to a generic
    sentence.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("object-store provider name must be non-empty")
    with _REGISTRY_LOCK:
        _REGISTRY[key] = factory
        if credential_hint is not None:
            _CREDENTIAL_HINTS[key] = credential_hint


def unregister_object_store(name: str) -> None:
    """Drop a registration (tests / experimental backends)."""
    with _REGISTRY_LOCK:
        key = name.strip().lower()
        _REGISTRY.pop(key, None)
        _CREDENTIAL_HINTS.pop(key, None)


def registered_providers() -> tuple[str, ...]:
    """Sorted provider names available, whether or not they are loaded yet."""
    with _REGISTRY_LOCK:
        names = set(_REGISTRY) | set(_BUILTIN_MODULES)
    return tuple(sorted(names))


def build_object_store(config: RemoteSyncConfig) -> ObjectStore:
    """Construct the store for ``config.provider``.

    Unknown names fail closed with the list of known providers so the operator
    can fix the env var without reading source. The factory runs outside the
    registry lock so a slow client build does not block other providers.
    """
    key = config.provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        factory = _REGISTRY.get(key)
        known = tuple(sorted(set(_REGISTRY) | set(_BUILTIN_MODULES)))
    if factory is None:
        listed = ", ".join(known) or "(none)"
        raise RemoteSyncConfigError(
            f"unknown remote-sync provider {config.provider!r}; known: {listed}"
        )
    return factory(config)


def credential_hint_for_provider(provider: str) -> str:
    """One line on ambient credentials for ``provider`` (no secrets).

    Loads a not-yet-imported built-in module first, so this works even before
    any store has been built for ``provider``.
    """
    key = provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        hint = _CREDENTIAL_HINTS.get(key)
    return hint if hint is not None else _DEFAULT_CREDENTIAL_HINT


__all__ = [
    "ObjectStoreFactory",
    "build_object_store",
    "credential_hint_for_provider",
    "register_object_store",
    "registered_providers",
    "unregister_object_store",
]
