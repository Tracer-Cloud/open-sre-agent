"""One investigation store per process, shared by the REST routes and the chat launcher.

Both callers must get the *same* object, and constructing one is not cheap:
``PostgresInvestigationStore`` runs the schema DDL in ``__init__`` and lazily opens a
pool of up to ten server connections that it never closes. Building one per request
would exhaust ``max_connections`` after a handful of chat investigations. The
in-memory store has the opposite failure — two instances means a record written by
one is invisible to the worker draining the other — so the cache is guarded by a lock
rather than a bare check-then-set.
"""

from __future__ import annotations

import threading

from config.constants.datastore import database_dsn
from gateway.core.storage.investigations.store import (
    InMemoryInvestigationStore,
    InvestigationStore,
)

_store_lock = threading.Lock()
_store: InvestigationStore | None = None


def get_investigation_store() -> InvestigationStore:
    """Return the process-wide investigation store, building it on first use."""
    global _store
    with _store_lock:
        if _store is None:
            _store = _build_store()
        return _store


def _build_store() -> InvestigationStore:
    """Pick Postgres when a DSN is configured, else the in-process fallback."""
    dsn = database_dsn()
    if not dsn:
        return InMemoryInvestigationStore()
    # Local import: the postgresql extra is optional.
    from gateway.core.storage.investigations.postgres import PostgresInvestigationStore

    return PostgresInvestigationStore(dsn)


def reset_investigation_store_for_tests() -> None:
    """Drop the cached store so a test can vary ``DATABASE_URI`` between cases."""
    global _store
    with _store_lock:
        _store = None
