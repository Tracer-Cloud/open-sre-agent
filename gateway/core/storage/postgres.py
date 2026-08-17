"""Pooled Postgres access shared by the gateway's cross-replica stores.

Selected when ``DATABASE_URL`` is set; requires the ``postgresql`` extra, which
is imported lazily so a process without it still starts.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_POOL_MIN_CONNECTIONS = 1
# Stores issue one short statement per request; a small pool covers a
# transport's concurrency without holding server connections open needlessly.
_POOL_MAX_CONNECTIONS = 5


class PostgresDatabase:
    """One lazily-opened threaded connection pool for a DSN."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS, _POOL_MAX_CONNECTIONS, self._dsn
                )
            return self._pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield a pooled connection; commit on success, roll back on error."""
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            with conn:
                yield conn
        finally:
            pool.putconn(conn)

    def run_schema(self, *statements: str) -> None:
        """Execute DDL statements in order inside one transaction."""
        with self.transaction() as conn, conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


__all__ = ["PostgresDatabase"]
