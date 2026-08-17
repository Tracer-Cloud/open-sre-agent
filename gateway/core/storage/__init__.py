"""Gateway persistence.

Session bindings are a JSON file on the org home (``session/``; paths in ``session/paths.py``). Records shared
across replicas — investigations and handled Slack events — are repositories:
each domain package holds its contract, a process-local implementation, a
Postgres implementation and a selector (``investigation_repository(database)``).
:func:`open_database` gives a process its one migrated
:class:`~gateway.core.storage.postgres.PostgresDatabase`, or ``None`` when
``DATABASE_URL`` is unset.

Transport-neutral on purpose. Slack principal resolution lives in
:mod:`gateway.transports.slack`; scope binding lives in :mod:`config.scope_context`.
This package neither imports a transport nor stands between callers and the
scope.
"""

from __future__ import annotations

from gateway.core.storage.migrations import apply_migrations
from gateway.core.storage.postgres import PostgresDatabase
from gateway.core.storage.session import FileBindingStore, SessionResolver
from gateway.core.storage.session.binding_store import open_binding_store, open_file_binding_store
from gateway.core.storage.session.paths import (
    bindings_file_path,
    gateway_dir,
)


def open_database() -> PostgresDatabase | None:
    """The process's migrated database when ``DATABASE_URL`` is set, else ``None``.

    Call once per process and hand the result to each repository selector; the
    pool is shared by every repository built on it.
    """
    database = PostgresDatabase.from_env()
    if database is not None:
        apply_migrations(database)
    return database


__all__ = [
    "FileBindingStore",
    "SessionResolver",
    "bindings_file_path",
    "gateway_dir",
    "open_binding_store",
    "open_database",
    "open_file_binding_store",
]
