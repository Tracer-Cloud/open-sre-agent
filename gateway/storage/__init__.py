"""Gateway persistence: session bindings and the host install catalog.

Transport-neutral on purpose. Slack-specific pieces — the install catalog
reader and principal resolution — live in :mod:`gateway.slack`, so this package
never imports a transport and a transport never has to go through it.
"""

from __future__ import annotations

from config.scope_context import bound_storage_scope, current_principal, current_scope
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
    "bound_storage_scope",
    "connect_bindings_db",
    "connect_gateway_db",
    "current_principal",
    "current_scope",
    "default_gateway_db_path",
    "gateway_dir",
    "open_binding_store",
]
