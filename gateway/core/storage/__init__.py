"""Gateway persistence: session bindings as a JSON file on the org home.

Transport-neutral on purpose. Slack principal resolution lives in
:mod:`gateway.transports.slack`; scope binding lives in :mod:`config.scope_context`.
This package neither imports a transport nor stands between callers and the
scope.
"""

from __future__ import annotations

from gateway.core.storage.db import (
    bindings_file_path,
    gateway_dir,
)
from gateway.core.storage.session import FileBindingStore, SessionResolver
from gateway.core.storage.session.binding_store import open_binding_store, open_file_binding_store

__all__ = [
    "FileBindingStore",
    "SessionResolver",
    "bindings_file_path",
    "gateway_dir",
    "open_binding_store",
    "open_file_binding_store",
]
