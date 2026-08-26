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


@dataclass(frozen=True)
class Engine:
    """One managed database engine."""

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
    """Default data-plane port."""

    integration: str
    """OpenSRE integration that queries this engine's data plane."""

    log_service_types: tuple[str, ...] = ()
    """``serviceType`` values the ``:logs`` endpoint accepts, first one default.

    An engine keeps several log streams and the endpoint serves one at a time.
    ClickHouse rejects a request without this outright; the rest answer with
    whichever stream they treat as primary, which is often not the interesting
    one — MySQL's error log rather than its slow-query log. Empty means the
    engine takes no such parameter.
    """


ENGINES: Final[tuple[Engine, ...]] = (
    # PostgreSQL answers on 6432, not 5432 — connections go through a pooler.
    Engine(
        "postgresql",
        "PostgreSQL",
        "managed-postgresql",
        "/managed-postgresql/v1",
        6432,
        "postgresql",
        ("POSTGRESQL", "POOLER", "REPACK"),
    ),
    Engine(
        "mysql",
        "MySQL",
        "managed-mysql",
        "/managed-mysql/v1",
        3306,
        "mysql",
        # Error log first: it is what explains a cluster that is misbehaving.
        ("MYSQL_ERROR", "MYSQL_GENERAL", "MYSQL_SLOW_QUERY", "MYSQL_AUDIT"),
    ),
    Engine(
        "clickhouse",
        "ClickHouse",
        "managed-clickhouse",
        "/managed-clickhouse/v1",
        8443,
        "clickhouse",
        # Required here: without it the endpoint refuses the read.
        ("CLICKHOUSE", "CLICKHOUSE_KEEPER"),
    ),
    Engine(
        "valkey",
        "Valkey (was Redis)",
        "managed-redis",
        "/managed-redis/v1",
        6380,
        "redis",
        ("REDIS",),
    ),
    Engine(
        "storedoc",
        "StoreDoc (was MongoDB)",
        "managed-mongodb",
        "/managed-mongodb/v1",
        27018,
        "mongodb",
        ("MONGOD", "MONGOS", "MONGOCFG", "AUDIT"),
    ),
    Engine("kafka", "Apache Kafka", "managed-kafka", "/managed-kafka/v1", 9091, "kafka"),
    Engine(
        "opensearch",
        "OpenSearch",
        "managed-opensearch",
        "/managed-opensearch/v1",
        9200,
        "opensearch",
        ("OPENSEARCH", "DASHBOARDS"),
    ),
    Engine(
        "greenplum",
        "MPP Analytics (was Greenplum)",
        "managed-greenplum",
        "/managed-greenplum/v1",
        6432,
        "postgresql",
        ("GREENPLUM", "GREENPLUM_POOLER", "GREENPLUM_PXF"),
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
}

ENGINE_KEYS: Final[tuple[str, ...]] = tuple(engine.key for engine in ENGINES)
_BY_KEY: Final[dict[str, Engine]] = {engine.key: engine for engine in ENGINES}


def resolve_engine(name: str) -> Engine | None:
    """Return the engine *name* refers to, accepting former product names."""
    key = name.strip().lower()
    return _BY_KEY.get(_ALIASES.get(key, key))


def engine_choices() -> str:
    """Return the accepted engine names, for an error message."""
    return ", ".join(ENGINE_KEYS)


__all__ = ["ENGINES", "ENGINE_KEYS", "Engine", "engine_choices", "resolve_engine"]
