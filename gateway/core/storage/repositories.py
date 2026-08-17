"""The gateway's repositories, built once per process from one database.

One place reads ``DATABASE_URL`` and decides between the Postgres
implementations (shared across replicas, one connection pool per process) and
the process-local ones. Hosts hold a :class:`Repositories` and hand out its
members; they do not construct stores themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from config.constants.gateway import DATABASE_URL_ENV
from gateway.core.storage.events.repository import (
    HandledSlackEventRepository,
    InMemoryHandledSlackEventRepository,
    PostgresHandledSlackEventRepository,
)
from gateway.core.storage.investigations.repository import (
    InMemoryInvestigationRepository,
    InvestigationRepository,
    PostgresInvestigationRepository,
)
from gateway.core.storage.migrations import apply_migrations
from gateway.core.storage.postgres import PostgresDatabase

# One pool serves every repository in the process: the investigation worker
# plus a burst of API threads, and one short statement per Slack delivery.
_POOL_MAX_CONNECTIONS = 10


@dataclass(frozen=True)
class Repositories:
    """Every repository the gateway uses, over one backing store."""

    investigations: InvestigationRepository
    handled_slack_events: HandledSlackEventRepository
    #: True when backed by Postgres and therefore correct across replicas.
    shared: bool

    @classmethod
    def from_env(cls) -> Repositories:
        """Postgres when ``DATABASE_URL`` is set, else process-local."""
        dsn = os.getenv(DATABASE_URL_ENV, "").strip()
        if not dsn:
            return cls.in_memory()
        database = PostgresDatabase(dsn, max_connections=_POOL_MAX_CONNECTIONS)
        apply_migrations(database)
        return cls(
            investigations=PostgresInvestigationRepository(database),
            handled_slack_events=PostgresHandledSlackEventRepository(database),
            shared=True,
        )

    @classmethod
    def in_memory(cls) -> Repositories:
        """Process-local repositories; correct only while exactly one replica runs."""
        return cls(
            investigations=InMemoryInvestigationRepository(),
            handled_slack_events=InMemoryHandledSlackEventRepository(),
            shared=False,
        )


__all__ = ["Repositories"]
