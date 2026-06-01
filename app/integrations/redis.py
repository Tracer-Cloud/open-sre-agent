"""Shared Redis integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Redis instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped, and key discovery uses the
non-blocking ``SCAN`` cursor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.integrations._validation_helpers import report_validation_failure
from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_MAX_RESULTS = 50
DEFAULT_REDIS_TIMEOUT_SECONDS = 5.0

# Hard cap on how many keys SCAN will iterate
DEFAULT_REDIS_SCAN_LIMIT = 10_000


class RedisConfig(StrictConfigModel):
    """Normalized Redis connection settings."""

    host: str = ""
    port: int = Field(default=DEFAULT_REDIS_PORT, ge=1, le=65535)
    username: str = ""
    password: str = ""
    db: int = Field(default=0, ge=0)
    ssl: bool = False
    timeout_seconds: float = Field(default=DEFAULT_REDIS_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_REDIS_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


@dataclass(frozen=True)
class RedisValidationResult:
    """Result of validating a Redis integration."""

    ok: bool
    detail: str


def build_redis_config(raw: dict[str, Any] | None) -> RedisConfig:
    """Build a normalized Redis config object from env/store data."""
    return RedisConfig.model_validate(raw or {})


def redis_config_from_env() -> RedisConfig | None:
    """Load a Redis config from env vars."""
    host = os.getenv("REDIS_HOST", "").strip()
    if not host:
        return None

    return build_redis_config(
        {
            "host": host,
            "port": os.getenv("REDIS_PORT", str(DEFAULT_REDIS_PORT)),
            "username": os.getenv("REDIS_USERNAME", "").strip(),
            "password": os.getenv("REDIS_PASSWORD", "").strip(),
            "db": os.getenv("REDIS_DATABASE", 0),
            "ssl": os.getenv("REDIS_SSL", "false").strip().lower() in ("true", "1", "yes"),
        }
    )


def _get_client(config: RedisConfig) -> Any:
    """Create a redis client from config. Caller must close.

    The client decodes responses to ``str`` so callers receive plain Python
    types rather than raw bytes.
    """
    import redis

    return redis.Redis(
        host=config.host,
        port=config.port,
        db=config.db,
        username=config.username or None,
        password=config.password or None,
        ssl=config.ssl,
        socket_timeout=config.timeout_seconds,
        socket_connect_timeout=config.timeout_seconds,
        decode_responses=True,
        client_name="opensre",
    )


def validate_redis_config(config: RedisConfig) -> RedisValidationResult:
    """Validate Redis connectivity with a lightweight ``PING`` command."""
    if not config.host:
        return RedisValidationResult(ok=False, detail="Redis host is required.")

    try:
        client = _get_client(config)
        try:
            if client.ping() is not True:
                return RedisValidationResult(
                    ok=False, detail="Redis PING returned an unexpected result."
                )
            info = client.info("server")
            version = info.get("redis_version", "unknown")
            return RedisValidationResult(
                ok=True,
                detail=(
                    f"Connected to Redis {version} at {config.host}:{config.port}; "
                    f"database {config.db}."
                ),
            )
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="redis",
            method="validate_redis_config",
        )
        return RedisValidationResult(ok=False, detail=f"Redis connection failed: {err}")


def redis_is_available(sources: dict[str, dict]) -> bool:
    """Check if Redis integration params are present in available sources."""
    return bool(sources.get("redis", {}).get("host"))


def redis_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Redis connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply the host or password directly.
    """
    rd = sources.get("redis", {})
    return {
        "host": str(rd.get("host", "")).strip(),
        "port": int(rd.get("port", DEFAULT_REDIS_PORT) or DEFAULT_REDIS_PORT),
        "username": str(rd.get("username", "")).strip(),
        "password": str(rd.get("password", "")).strip(),
        "db": int(rd.get("db", 0) or 0),
        "ssl": bool(rd.get("ssl", False)),
    }


def get_server_info(config: RedisConfig) -> dict[str, Any]:
    """Retrieve server info: memory, connected clients, keyspace, and stats.

    Read-only: uses the ``INFO`` command.
    """
    if not config.is_configured:
        return {"source": "redis", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            info = client.info()
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_server_info")

    keyspace = {
        db_name: {
            "keys": db_stats.get("keys", 0),
            "expires": db_stats.get("expires", 0),
            "avg_ttl_ms": db_stats.get("avg_ttl", 0),
        }
        for db_name, db_stats in info.items()
        if db_name.startswith("db") and isinstance(db_stats, dict)
    }
    return {
        "source": "redis",
        "available": True,
        "version": info.get("redis_version", ""),
        "mode": info.get("redis_mode", ""),
        "uptime_seconds": info.get("uptime_in_seconds", 0),
        "memory": {
            "used_memory_bytes": info.get("used_memory", 0),
            "used_memory_human": info.get("used_memory_human", ""),
            "used_memory_rss_bytes": info.get("used_memory_rss", 0),
            "used_memory_peak_bytes": info.get("used_memory_peak", 0),
            "maxmemory_bytes": info.get("maxmemory", 0),
            "maxmemory_policy": info.get("maxmemory_policy", ""),
            "mem_fragmentation_ratio": info.get("mem_fragmentation_ratio", 0),
        },
        "clients": {
            "connected_clients": info.get("connected_clients", 0),
            "blocked_clients": info.get("blocked_clients", 0),
            "tracking_clients": info.get("tracking_clients", 0),
        },
        "stats": {
            "total_connections_received": info.get("total_connections_received", 0),
            "total_commands_processed": info.get("total_commands_processed", 0),
            "instantaneous_ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "expired_keys": info.get("expired_keys", 0),
            "evicted_keys": info.get("evicted_keys", 0),
            "rejected_connections": info.get("rejected_connections", 0),
        },
        "keyspace": keyspace,
    }


def get_slowlog(config: RedisConfig, limit: int | None = None) -> dict[str, Any]:
    """Retrieve recent slow log entries.

    Read-only: uses ``SLOWLOG GET``.  Durations are reported in microseconds
    (as Redis stores them).  Results are capped at ``config.max_results``.
    """
    if not config.is_configured:
        return {"source": "redis", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            raw_entries = client.slowlog_get(effective_limit)
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_slowlog")

    entries = []
    for entry in raw_entries:
        command = entry.get("command", "")
        if isinstance(command, (bytes, bytearray)):
            command = command.decode("utf-8", "replace")
        entries.append(
            {
                "id": entry.get("id"),
                "start_time": entry.get("start_time"),
                "duration_microseconds": entry.get("duration", 0),
                "command": str(command),
                "client_address": entry.get("client_address", ""),
                "client_name": entry.get("client_name", ""),
            }
        )
    return {
        "source": "redis",
        "available": True,
        "returned_entries": len(entries),
        "entries": entries,
    }


def get_replication(config: RedisConfig) -> dict[str, Any]:
    """Retrieve replication status and replica lag.

    Read-only: uses ``INFO replication``.  Reports the node role, master link
    health (for replicas), and per-replica offset lag (for masters).
    """
    if not config.is_configured:
        return {"source": "redis", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            info = client.info("replication")
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_replication")

    role = info.get("role", "")
    master_repl_offset = info.get("master_repl_offset", 0)
    result: dict[str, Any] = {
        "source": "redis",
        "available": True,
        "role": role,
        "connected_slaves": info.get("connected_slaves", 0),
        "master_repl_offset": master_repl_offset,
    }

    if role == "slave":
        slave_offset = info.get("slave_repl_offset", 0)
        result["master"] = {
            "host": info.get("master_host", ""),
            "port": info.get("master_port", 0),
            "link_status": info.get("master_link_status", ""),
            "last_io_seconds_ago": info.get("master_last_io_seconds_ago", -1),
            "sync_in_progress": bool(info.get("master_sync_in_progress", 0)),
            "slave_repl_offset": slave_offset,
        }

    replicas = []
    for key, value in info.items():
        if not key.startswith("slave") or not isinstance(value, dict):
            continue
        replica_offset = value.get("offset", 0)
        replicas.append(
            {
                "id": key,
                "ip": value.get("ip", ""),
                "port": value.get("port", 0),
                "state": value.get("state", ""),
                "offset": replica_offset,
                "lag_bytes": max(0, master_repl_offset - replica_offset),
            }
        )
    result["replicas"] = replicas
    return result


def scan_keys(
    config: RedisConfig,
    pattern: str = "*",
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Count keys matching a pattern and sample their TTL and type.

    Read-only: uses the non-blocking ``SCAN`` cursor (never ``KEYS``) so the
    server is not blocked on large keyspaces.  Total iteration is capped at
    ``DEFAULT_REDIS_SCAN_LIMIT``; TTL/type sampling is capped at
    ``config.max_results``.
    """
    if not config.is_configured:
        return {"source": "redis", "available": False, "error": "Not configured."}

    match = pattern or "*"
    sample_cap = min(sample_limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            cursor, matched = 0, 0
            truncated = False
            samples: list[dict[str, Any]] = []
            while True:
                cursor, batch = client.scan(cursor=cursor, match=match, count=100)
                for key in batch:
                    matched += 1
                    if len(samples) < sample_cap:
                        ttl = client.ttl(key)
                        samples.append(
                            {
                                "key": key,
                                "ttl_seconds": ttl,  # -1 = no expiry, -2 = missing
                                "type": client.type(key),
                            }
                        )
                    if matched >= DEFAULT_REDIS_SCAN_LIMIT:
                        truncated = True
                        break
                if cursor == 0 or truncated:
                    break
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "scan_keys")

    return {
        "source": "redis",
        "available": True,
        "pattern": match,
        "matched_keys": matched,
        "scan_truncated": truncated,
        "scan_limit": DEFAULT_REDIS_SCAN_LIMIT,
        "sampled_keys": len(samples),
        "samples": samples,
    }


def _redis_error(err: Exception, method: str) -> dict[str, Any]:
    """Normalize a Redis exception into a graceful, available=False payload.

    Authentication and permission failures return a friendly hint without a
    Sentry report; all other errors are reported for diagnosis.  Errors are
    classified by redis-py's typed exceptions rather than message substrings.
    """
    import redis.exceptions as redis_exc

    if isinstance(err, redis_exc.AuthenticationError):
        return {
            "source": "redis",
            "available": False,
            "error": "Redis authentication failed. Check the credentials in the connection settings.",
        }
    if isinstance(err, redis_exc.NoPermissionError):
        return {
            "source": "redis",
            "available": False,
            "error": (
                "Redis user lacks permission for this command. "
                "Grant read access to the INFO, SLOWLOG, and SCAN commands."
            ),
        }
    report_validation_failure(
        err,
        logger=logger,
        integration="redis",
        method=method,
    )
    return {"source": "redis", "available": False, "error": str(err)}
