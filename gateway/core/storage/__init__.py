"""Gateway persistence.

Session bindings are a JSON file on the org home (``session/``; paths in ``session/paths.py``). Records shared
across replicas — investigations and handled Slack events — are repositories:
each domain package holds its contract, a process-local implementation and a
Postgres implementation; :class:`~gateway.core.storage.repositories.Repositories`
is the one place that chooses between them and shares one
:class:`~gateway.core.storage.postgres.PostgresDatabase` per process.

Transport-neutral on purpose. Slack principal resolution lives in
:mod:`gateway.transports.slack`; scope binding lives in :mod:`config.scope_context`.
This package neither imports a transport nor stands between callers and the
scope.
"""

from __future__ import annotations

from gateway.core.storage.session import FileBindingStore, SessionResolver
from gateway.core.storage.session.binding_store import open_binding_store, open_file_binding_store
from gateway.core.storage.session.paths import (
    bindings_file_path,
    gateway_dir,
)

__all__ = [
    "FileBindingStore",
    "SessionResolver",
    "bindings_file_path",
    "gateway_dir",
    "open_binding_store",
    "open_file_binding_store",
]
