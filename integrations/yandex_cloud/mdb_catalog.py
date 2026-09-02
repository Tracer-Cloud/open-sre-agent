"""The managed database engines, and how to reach each one.

Every engine has its own endpoint registry key and REST prefix even though they
all resolve to the same host. Several were renamed while keeping the old API
paths — MongoDB is now StoreDoc, Redis is Valkey, Greenplum is MPP Analytics —
so the display name and the path deliberately differ.

Data-plane ports are here because the most useful thing an investigation can do
with a managed database is stop looking at the control plane and go query it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Carried in every tool description of this family rather than as the full
#: SKILL.md slice: the slice is 2400 characters and would be duplicated into each
#: description, while the one thing an investigation loses without it is this
#: sentence. A read-only integration cannot change anything, so the command it
#: writes out is the remediation - and an investigation that only touched these
#: tools never saw the generic readers where the slice is attached.
READ_ONLY_HANDOFF: Final = "These tools only read, so when the finding calls for a change, end with the exact `yc ...` command an operator can paste."


@dataclass(frozen=True)
class ManagedDatabase:
    """One managed database engine, and how to reach it."""

    key: str
    """What a caller names, e.g. ``postgresql``."""

    label: str
    """Current product name, which is not always the key."""

    service: str
    """Endpoint registry key.

    The ``managed-`` form rather than ``mdb-``: the registry carries ``mdb-``
    for only six of the eight engines, and a missing key means the read is
    refused before it is sent.
    """

    path: str
    """REST path prefix."""

    port: int
    """Data-plane port with TLS on, which is how a managed cluster is normally set up.

    Naming it plainly because several engines listen on two: Valkey answers TLS
    on 6380 and plaintext on 6379, StoreDoc on 27018 and 27017, ClickHouse on
    8443 and 8123. Handing an operator the wrong one produces a connection
    timeout that reads exactly like a dead database.
    """

    integration: str
    """OpenSRE integration that queries this engine's data plane."""

    host_collections: tuple[str, ...] = ("hosts",)
    """Where this engine lists its hosts, relative to the cluster.

    Almost every engine answers on ``hosts``. Greenplum does not have that
    collection at all: its hosts are split into ``master-hosts`` and
    ``segment-hosts``, and asking for ``hosts`` returns a 404 that leaves the
    cluster looking like it has no hosts and no address to connect to.
    """

    plaintext_port: int | None = None
    """The same port with TLS off, when the engine listens on a different one."""

    log_service_types: tuple[str, ...] = ()
    """``serviceType`` values the ``:logs`` endpoint accepts, first one default.

    An engine keeps several log streams and the endpoint serves one at a time.
    ClickHouse rejects a request without this outright; the rest answer with
    whichever stream they treat as primary, which is often not the interesting
    one — MySQL's error log rather than its slow-query log. Empty means the
    engine takes no such parameter.
    """


ENGINES: Final[tuple[ManagedDatabase, ...]] = (
    # PostgreSQL answers on 6432, not 5432 — connections go through a pooler.
    ManagedDatabase(
        "postgresql",
        "PostgreSQL",
        "managed-postgresql",
        "/managed-postgresql/v1",
        6432,
        "postgresql",
        log_service_types=("POSTGRESQL", "POOLER", "REPACK"),
    ),
    ManagedDatabase(
        "mysql",
        "MySQL",
        "managed-mysql",
        "/managed-mysql/v1",
        3306,
        "mysql",
        # Error log first: it is what explains a cluster that is misbehaving.
        log_service_types=("MYSQL_ERROR", "MYSQL_GENERAL", "MYSQL_SLOW_QUERY", "MYSQL_AUDIT"),
    ),
    ManagedDatabase(
        "clickhouse",
        "ClickHouse",
        "managed-clickhouse",
        "/managed-clickhouse/v1",
        8443,
        "clickhouse",
        plaintext_port=8123,
        # Required here: without it the endpoint refuses the read.
        log_service_types=("CLICKHOUSE", "CLICKHOUSE_KEEPER"),
    ),
    ManagedDatabase(
        "valkey",
        "Valkey (was Redis)",
        "managed-redis",
        "/managed-redis/v1",
        6380,
        "redis",
        plaintext_port=6379,
        log_service_types=("REDIS",),
    ),
    ManagedDatabase(
        "storedoc",
        "StoreDoc (was MongoDB)",
        "managed-mongodb",
        "/managed-mongodb/v1",
        27018,
        "mongodb",
        plaintext_port=27017,
        log_service_types=("MONGOD", "MONGOS", "MONGOCFG", "AUDIT"),
    ),
    ManagedDatabase(
        "kafka",
        "Apache Kafka",
        "managed-kafka",
        "/managed-kafka/v1",
        9091,
        "kafka",
        plaintext_port=9092,
    ),
    ManagedDatabase(
        "opensearch",
        "OpenSearch",
        "managed-opensearch",
        "/managed-opensearch/v1",
        9200,
        "opensearch",
        log_service_types=("OPENSEARCH", "DASHBOARDS"),
    ),
    ManagedDatabase(
        "greenplum",
        "MPP Analytics (was Greenplum)",
        "managed-greenplum",
        "/managed-greenplum/v1",
        6432,
        "postgresql",
        # The master is what a client connects to; segments are where the work
        # happens, and a sick segment is what makes a query hang.
        ("master-hosts", "segment-hosts"),
        log_service_types=("GREENPLUM", "GREENPLUM_POOLER", "GREENPLUM_PXF"),
    ),
    # Sharded PostgreSQL is its own service, not a mode of the one above: its
    # own API prefix, its own cluster type, and a router in front of the shards.
    ManagedDatabase(
        "spqr",
        "Sharded PostgreSQL (SPQR)",
        "managed-spqr",
        "/managed-spqr/v1",
        6432,
        "postgresql",
        ("hosts",),
        log_service_types=("POSTGRESQL", "ROUTER", "COORDINATOR", "INFRA"),
    ),
)

#: Old names people still type, mapped to the current key.
_ALIASES: Final[dict[str, str]] = {
    "postgres": "postgresql",
    "pg": "postgresql",
    "redis": "valkey",
    "mongodb": "storedoc",
    "mongo": "storedoc",
    "mpp": "greenplum",
    "sharded postgresql": "spqr",
    "sharded-postgresql": "spqr",
}

ENGINE_KEYS: Final[tuple[str, ...]] = tuple(engine.key for engine in ENGINES)
_BY_KEY: Final[dict[str, ManagedDatabase]] = {engine.key: engine for engine in ENGINES}


def resolve_engine(name: str) -> ManagedDatabase | None:
    """Return the engine *name* refers to, accepting former product names."""
    key = name.strip().lower()
    return _BY_KEY.get(_ALIASES.get(key, key))


def engine_choices() -> str:
    """Return the accepted engine names, for an error message."""
    return ", ".join(ENGINE_KEYS)


__all__ = [
    "ENGINES",
    "READ_ONLY_HANDOFF",
    "ENGINE_KEYS",
    "ManagedDatabase",
    "engine_choices",
    "resolve_engine",
]
