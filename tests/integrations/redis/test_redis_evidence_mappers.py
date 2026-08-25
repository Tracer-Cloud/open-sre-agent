"""Unit tests for Redis evidence mappers.

Arrange/Act/Assert pattern — each test feeds a sample output dict into the
mapper directly (no Redis connection needed) and asserts that a catalog entry
is recorded with the expected source.
"""

from __future__ import annotations

from typing import Any

from integrations.redis.tools.redis_client_list_tool import get_redis_client_list
from integrations.redis.tools.redis_latency_doctor_tool import get_redis_latency_doctor
from integrations.redis.tools.redis_list_depth_tool import get_redis_list_depth
from integrations.redis.tools.redis_replication_tool import get_redis_replication
from integrations.redis.tools.redis_server_info_tool import get_redis_server_info
from integrations.redis.tools.redis_slowlog_tool import get_redis_slowlog


def _get_mapper(registered_tool: Any):
    """Pull the evidence mapper from a registered @tool object"""
    return registered_tool.__opensre_registered_tool__.evidence_mapper


class TestRedisClientListMapper:
    def test_records_entry_when_clients_present(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_client_list)
        output = {
            "available": True,
            "total_clients": 47,
            "blocked_clients": 3,
            "clients": [{"id": 1, "addr": "127:0:0:1:5000", "command": "GET"}],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_client_list" for e in entries), (
            "Expected a catalog entry with source='get_redis_client_list'"
        )
        entry = next(e for e in entries if e["source"] == "get_redis_client_list")
        assert "47" in entry["summary"]
        assert "3" in entry["summary"]

    def test_no_entry_when_clients_empty(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_client_list)
        output = {"available": True, "total_clients": 0, "blocked_clients": 0, "clients": []}
        evidence: dict[str, Any] = {}
        # Act
        mapper(evidence, output, {})
        # Assert
        assert "catalog_entries" not in evidence


class TestRedisLatencyDoctorMapper:
    def test_records_entry_when_events_present(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_latency_doctor)
        output = {
            "available": True,
            "report": "I have the following advice...",
            "latest": [
                {"event": "command", "last_occurrence": 1000, "latest_ms": 120, "max_ms": 340}
            ],
            "history": [],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_latency_doctor" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_latency_doctor")
        assert "1" in entry["summary"]

    def test_records_entry_when_no_events_but_report_present(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_latency_doctor)
        output = {
            "available": True,
            "report": "I have no issues to report",
            "latest": [],
            "history": [],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_latency_doctor" for e in entries)

    def test_no_entry_when_output_empty(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_latency_doctor)
        output = {"available": True, "latest": [], "report": ""}
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence


class TestRedisListDepthMapper:
    def test_records_entry_when_key_exists(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_list_depth)
        output = {
            "available": True,
            "key": "celery:default",
            "exists": True,
            "type": "list",
            "depth": 1234,
            "head": [],
            "tail": [],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_list_depth" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_list_depth")
        assert "1234" in entry["summary"]
        assert "celery:default" in entry["summary"]

    def test_no_entry_when_key_missing(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_list_depth)
        output = {
            "available": True,
            "key": "celery:default",
            "exists": False,
            "type": "none",
            "depth": 0,
            "head": [],
            "tail": [],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence

    def test_no_entry_when_depth_is_none(self) -> None:
        # Arrange — key exists but is wrong type (not a list)
        mapper = _get_mapper(get_redis_list_depth)
        output = {
            "available": True,
            "key": "some:hash",
            "exists": True,
            "type": "hash",
            "depth": None,
            "head": [],
            "tail": [],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence


class TestRedisReplicationMapper:
    def test_records_entry_for_master_with_replicas(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_replication)
        output = {
            "available": True,
            "role": "master",
            "connected_slaves": 2,
            "master_repl_offset": 10000,
            "replicas": [
                {
                    "id": "slave0",
                    "ip": "10.0.0.2",
                    "port": 6379,
                    "state": "online",
                    "offset": 9990,
                    "lag_bytes": 10,
                },
                {
                    "id": "slave1",
                    "ip": "10.0.0.3",
                    "port": 6379,
                    "state": "online",
                    "offset": 9980,
                    "lag_bytes": 20,
                },
            ],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_replication" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_replication")
        assert "master" in entry["summary"]
        assert "2" in entry["summary"]

    def test_records_entry_for_replica_node(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_replication)
        output = {
            "available": True,
            "role": "slave",
            "connected_slaves": 0,
            "master_repl_offset": 5000,
            "replicas": [],
            "master": {
                "host": "10.0.0.1",
                "port": 6379,
                "link_status": "up",
                "last_io_seconds_ago": 1,
                "sync_in_progress": False,
                "slave_repl_offset": 4990,
            },
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_replication" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_replication")
        assert "slave" in entry["summary"]

    def test_no_entry_when_role_missing(self) -> None:
        # Arrange — tool returned unavailable / error payload
        mapper = _get_mapper(get_redis_replication)
        output = {"available": False, "role": "", "replicas": []}
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence


class TestRedisServerInfoMapper:
    def test_records_entry_with_memory_and_clients(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_server_info)
        output = {
            "available": True,
            "version": "7.0.5",
            "memory": {
                "used_memory_bytes": 536870912,
                "used_memory_human": "512.00M",
                "used_memory_rss_bytes": 600000000,
                "used_memory_peak_bytes": 540000000,
                "maxmemory_bytes": 1073741824,
                "maxmemory_policy": "allkeys-lru",
                "mem_fragmentation_ratio": 1.12,
            },
            "clients": {
                "connected_clients": 24,
                "blocked_clients": 1,
                "tracking_clients": 0,
            },
            "stats": {
                "keyspace_hits": 10000,
                "keyspace_misses": 500,
                "evicted_keys": 0,
                "expired_keys": 120,
                "total_connections_received": 9999,
                "total_commands_processed": 88888,
                "instantaneous_ops_per_sec": 42,
                "rejected_connections": 0,
            },
            "keyspace": {"db0": {"keys": 500, "expires": 100, "avg_ttl_ms": 3600000}},
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_server_info" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_server_info")
        assert "512.00M" in entry["summary"]
        assert "24" in entry["summary"]

    def test_records_entry_with_evictions(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_server_info)
        output = {
            "available": True,
            "memory": {"used_memory_human": "256.00M"},
            "clients": {"connected_clients": 5, "blocked_clients": 0, "tracking_clients": 0},
            "stats": {
                "evicted_keys": 300,
                "keyspace_hits": 0,
                "keyspace_misses": 0,
                "expired_keys": 0,
                "total_connections_received": 0,
                "total_commands_processed": 0,
                "instantaneous_ops_per_sec": 0,
                "rejected_connections": 0,
            },
            "keyspace": {},
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_server_info" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_server_info")
        assert "300" in entry["summary"]

    def test_no_entry_when_memory_and_clients_absent(self) -> None:
        # Arrange — tool returned an error/unavailable payload
        mapper = _get_mapper(get_redis_server_info)
        output = {"available": False, "memory": {}, "clients": {}}
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence


class TestRedisSlowlogMapper:
    def test_records_entry_when_entries_present(self) -> None:
        # Arrange
        mapper = _get_mapper(get_redis_slowlog)
        output = {
            "available": True,
            "returned_entries": 3,
            "entries": [
                {
                    "id": 1,
                    "start_time": 1700000001,
                    "duration_microseconds": 12000,
                    "command": "KEYS *",
                    "client_address": "10.0.0.1:5001",
                    "client_name": "",
                },
                {
                    "id": 2,
                    "start_time": 1700000002,
                    "duration_microseconds": 8500,
                    "command": "LRANGE queue 0 -1",
                    "client_address": "10.0.0.2:5002",
                    "client_name": "",
                },
                {
                    "id": 3,
                    "start_time": 1700000003,
                    "duration_microseconds": 6200,
                    "command": "SMEMBERS bigset",
                    "client_address": "10.0.0.1:5001",
                    "client_name": "",
                },
            ],
        }
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        entries = evidence.get("catalog_entries", [])
        assert any(e["source"] == "get_redis_slowlog" for e in entries)
        entry = next(e for e in entries if e["source"] == "get_redis_slowlog")
        assert "3" in entry["summary"]

    def test_no_entry_when_entries_empty(self) -> None:
        # Arrange — slowlog is empty (no slow commands recorded)
        mapper = _get_mapper(get_redis_slowlog)
        output = {"available": True, "returned_entries": 0, "entries": []}
        evidence: dict[str, Any] = {}

        # Act
        mapper(evidence, output, {})

        # Assert
        assert "catalog_entries" not in evidence
