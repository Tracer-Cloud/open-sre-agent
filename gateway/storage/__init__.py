"""Gateway persistence: session bindings and the host install catalog.

Transport-neutral on purpose. Slack-specific pieces — the install catalog
reader and principal resolution — live in :mod:`gateway.slack`, and the scope
binding itself lives in :mod:`config.scope_context`, so this package neither
imports a transport nor stands between callers and the scope.
"""

from __future__ import annotations

from gateway.storage.db import (
    bindings_db_path,
    connect_bindings_db,
    connect_gateway_db,
    default_gateway_db_path,
    gateway_dir,
)
from gateway.storage.session import SessionBindingStore, SessionResolver
from gateway.storage.session.binding_store import open_binding_store

__all__ = [
    "SessionBindingStore",
    "SessionResolver",
    "bindings_db_path",
    "connect_bindings_db",
    "connect_gateway_db",
    "default_gateway_db_path",
    "gateway_dir",
    "open_binding_store",
]
