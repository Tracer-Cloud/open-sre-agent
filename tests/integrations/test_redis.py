"""Unit tests for Redis integration."""

import os
from unittest.mock import MagicMock, patch

from app.integrations.catalog import classify_integrations as _classify_integrations
from app.integrations.redis import (
    RedisConfig,
    build_redis_config,
    get_client_list,
    get_latency_doctor,
    get_list_depth,
    get_replication,
    get_server_info,
    get_slowlog,
    redis_config_from_env,
    redis_extract_params,
    scan_keys,
    validate_redis_config,
)


class TestRedisConfig:
    def test_default_values(self):
        config = RedisConfig(host="localhost")
        assert config.port == 6379
        assert config.username == ""
        assert config.password == ""
        assert config.db == 0
        assert config.ssl is False
        assert config.timeout_seconds == 5.0
        assert config.max_results == 50

    def test_normalization(self):
        config = RedisConfig(host="  localhost  ", username="  acl  ", password="  hunter2  ")
        assert config.host == "localhost"
        assert config.username == "acl"
        assert config.password == "hunter2"

    def test_is_configured(self):
        assert RedisConfig(host="localhost").is_configured is True
        assert RedisConfig(host="").is_configured is False


class TestRedisBuild:
    def test_build_redis_config(self):
        raw = {
            "host": "cache.example.net",
            "port": 6380,
            "username": "monitor",
            "password": "p",
            "db": 3,
            "ssl": True,
        }
        config = build_redis_config(raw)
        assert config.host == "cache.example.net"
        assert config.port == 6380
        assert config.username == "monitor"
        assert config.password == "p"
        assert config.db == 3
        assert config.ssl is True

    @patch.dict(
        os.environ,
        {
            "REDIS_HOST": "env-host",
            "REDIS_PORT": "6380",
            "REDIS_USERNAME": "env-user",
            "REDIS_PASSWORD": "env-pass",
            "REDIS_DATABASE": "2",
            "REDIS_SSL": "true",
        },
    )
    def test_redis_config_from_env(self):
        config = redis_config_from_env()
        assert config is not None
        assert config.host == "env-host"
        assert config.port == 6380
        assert config.username == "env-user"
        assert config.password == "env-pass"
        assert config.db == 2
        assert config.ssl is True

    @patch.dict(os.environ, {}, clear=True)
    def test_redis_config_from_env_missing(self):
        assert redis_config_from_env() is None


class TestRedisExtractParams:
    def test_extract_params(self):
        sources = {
            "redis": {
                "host": "cache",
                "port": 6380,
                "username": "u",
                "password": "p",
                "db": 1,
                "ssl": True,
            },
        }
        params = redis_extract_params(sources)
        assert params == {
            "host": "cache",
            "port": 6380,
            "username": "u",
            "password": "p",
            "db": 1,
            "ssl": True,
        }

    def test_extract_params_missing_source(self):
        params = redis_extract_params({})
        assert params["host"] == ""
        assert params["port"] == 6379
        assert params["db"] == 0
        assert params["ssl"] is False


class TestRedisValidation:
    @patch("app.integrations.redis._get_client")
    def test_validate_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.info.return_value = {"redis_version": "7.2.4"}
        mock_get_client.return_value = mock_client

        result = validate_redis_config(RedisConfig(host="cache", port=6379, db=0))

        assert result.ok is True
        assert "7.2.4" in result.detail
        assert "cache:6379" in result.detail
        mock_client.close.assert_called_once()

    @patch("app.integrations.redis._get_client")
    def test_validate_ping_failure(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.ping.return_value = False
        mock_get_client.return_value = mock_client

        result = validate_redis_config(RedisConfig(host="cache"))

        assert result.ok is False
        assert "unexpected result" in result.detail

    def test_validate_missing_host(self):
        result = validate_redis_config(RedisConfig(host=""))
        assert result.ok is False
        assert "required" in result.detail

    @patch("app.integrations.redis._get_client", side_effect=Exception("Conn error"))
    def test_validate_exception(self, _):
        result = validate_redis_config(RedisConfig(host="cache"))
        assert result.ok is False
        assert "Conn error" in result.detail


class TestRedisServerInfo:
    @patch("app.integrations.redis._get_client")
    def test_get_server_info(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.info.return_value = {
            "redis_version": "7.2.4",
            "uptime_in_seconds": 1000,
            "used_memory": 1048576,
            "used_memory_human": "1.00M",
            "maxmemory_policy": "allkeys-lru",
            "connected_clients": 12,
            "keyspace_hits": 900,
            "keyspace_misses": 100,
            "evicted_keys": 5,
            "db0": {"keys": 42, "expires": 10, "avg_ttl": 5000},
        }
        mock_get_client.return_value = mock_client

        result = get_server_info(RedisConfig(host="cache"))

        assert result["available"] is True
        assert result["version"] == "7.2.4"
        assert result["memory"]["used_memory_bytes"] == 1048576
        assert result["clients"]["connected_clients"] == 12
        assert result["stats"]["evicted_keys"] == 5
        assert result["keyspace"]["db0"]["keys"] == 42
        mock_client.close.assert_called_once()

    def test_get_server_info_not_configured(self):
        result = get_server_info(RedisConfig(host=""))
        assert result["available"] is False


class TestRedisSlowlog:
    @patch("app.integrations.redis._get_client")
    def test_get_slowlog(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.slowlog_get.return_value = [
            {
                "id": 1,
                "start_time": 1700000000,
                "duration": 12345,
                "command": "GET foo",
                "client_address": "127.0.0.1:5000",
                "client_name": "",
            }
        ]
        mock_get_client.return_value = mock_client

        result = get_slowlog(RedisConfig(host="cache"), limit=10)

        assert result["available"] is True
        assert result["returned_entries"] == 1
        assert result["entries"][0]["duration_microseconds"] == 12345
        assert result["entries"][0]["command"] == "GET foo"

    @patch("app.integrations.redis._get_client")
    def test_get_slowlog_decodes_bytes_command(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.slowlog_get.return_value = [
            {"id": 1, "start_time": 1, "duration": 1, "command": b"GET bar"}
        ]
        mock_get_client.return_value = mock_client

        result = get_slowlog(RedisConfig(host="cache"))
        assert result["entries"][0]["command"] == "GET bar"


class TestRedisReplication:
    @patch("app.integrations.redis._get_client")
    def test_master_with_replica_lag(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.info.return_value = {
            "role": "master",
            "connected_slaves": 1,
            "master_repl_offset": 1000,
            "slave0": {"ip": "10.0.0.2", "port": 6379, "state": "online", "offset": 800},
        }
        mock_get_client.return_value = mock_client

        result = get_replication(RedisConfig(host="cache"))

        assert result["available"] is True
        assert result["role"] == "master"
        assert result["replicas"][0]["lag_bytes"] == 200

    @patch("app.integrations.redis._get_client")
    def test_slave_reports_master_link(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.info.return_value = {
            "role": "slave",
            "connected_slaves": 0,
            "master_repl_offset": 0,
            "master_host": "10.0.0.1",
            "master_port": 6379,
            "master_link_status": "up",
            "slave_repl_offset": 950,
        }
        mock_get_client.return_value = mock_client

        result = get_replication(RedisConfig(host="cache"))

        assert result["role"] == "slave"
        assert result["master"]["link_status"] == "up"


class TestRedisScanKeys:
    @patch("app.integrations.redis._get_client")
    def test_scan_counts_and_samples(self, mock_get_client):
        mock_client = MagicMock()
        # One SCAN round then cursor 0 to terminate.
        mock_client.scan.return_value = (0, ["session:1", "session:2"])
        mock_client.pipeline.return_value.execute.return_value = [60, "string", -1, "hash"]
        mock_get_client.return_value = mock_client

        result = scan_keys(RedisConfig(host="cache"), pattern="session:*")

        assert result["available"] is True
        assert result["pattern"] == "session:*"
        assert result["matched_keys"] == 2
        assert result["scan_truncated"] is False
        assert result["samples"][0] == {"key": "session:1", "ttl_seconds": 60, "type": "string"}
        assert result["samples"][1] == {"key": "session:2", "ttl_seconds": -1, "type": "hash"}
        mock_client.pipeline.assert_called_once_with(transaction=False)

    @patch("app.integrations.redis._get_client")
    def test_scan_respects_sample_cap_within_a_single_page(self, mock_get_client):
        # A single SCAN page larger than the cap must not oversample: sampling
        # is capped at config.max_results even when one page exceeds it.
        mock_client = MagicMock()
        mock_client.scan.return_value = (0, [f"k{i}" for i in range(60)])
        mock_client.pipeline.return_value.execute.return_value = [1, "string"] * 60
        mock_get_client.return_value = mock_client

        result = scan_keys(RedisConfig(host="cache", max_results=50))

        assert result["matched_keys"] == 60  # all matches counted
        assert result["sampled_keys"] == 50  # but sampling stays capped
        assert len(result["samples"]) == 50

    @patch("app.integrations.redis.report_validation_failure")
    @patch("app.integrations.redis._get_client")
    def test_scan_auth_error_is_graceful_without_sentry(self, mock_get_client, mock_report):
        import redis.exceptions as redis_exc

        mock_client = MagicMock()
        mock_client.scan.side_effect = redis_exc.AuthenticationError("WRONGPASS bad pair")
        mock_get_client.return_value = mock_client

        result = scan_keys(RedisConfig(host="cache"))
        assert result["available"] is False
        assert "authentication" in result["error"].lower()
        mock_report.assert_not_called()

    @patch("app.integrations.redis.report_validation_failure")
    @patch("app.integrations.redis._get_client")
    def test_scan_noperm_error_is_graceful_without_sentry(self, mock_get_client, mock_report):
        import redis.exceptions as redis_exc

        mock_client = MagicMock()
        mock_client.scan.side_effect = redis_exc.NoPermissionError("NOPERM no read access")
        mock_get_client.return_value = mock_client

        result = scan_keys(RedisConfig(host="cache"))
        assert result["available"] is False
        assert "permission" in result["error"].lower()
        mock_report.assert_not_called()

    @patch("app.integrations.redis.report_validation_failure")
    @patch("app.integrations.redis._get_client")
    def test_scan_other_error_reports_sentry(self, mock_get_client, mock_report):
        mock_client = MagicMock()
        mock_client.scan.side_effect = Exception("connection reset")
        mock_get_client.return_value = mock_client

        result = scan_keys(RedisConfig(host="cache"))
        assert result["available"] is False
        assert "connection reset" in result["error"]
        mock_report.assert_called_once()


class TestRedisClientList:
    @patch("app.integrations.redis._get_client")
    def test_aggregates_blocked_pubsub_and_breakdowns(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.client_list.return_value = [
            {
                "id": "1",
                "addr": "10.0.0.1:5000",
                "flags": "N",
                "idle": "0",
                "cmd": "get",
                "sub": "0",
            },
            {
                "id": "2",
                "addr": "10.0.0.1:5001",
                "flags": "b",
                "idle": "2",
                "cmd": "blpop",
                "sub": "0",
            },
            {
                "id": "3",
                "addr": "10.0.0.2:6000",
                "flags": "P",
                "idle": "120",
                "cmd": "subscribe",
                "sub": "3",
            },
        ]
        mock_get_client.return_value = mock_client

        result = get_client_list(RedisConfig(host="cache"))

        assert result["available"] is True
        assert result["total_clients"] == 3
        assert result["blocked_clients"] == 1  # the "b"-flagged blpop client
        assert result["pubsub_clients"] == 1  # the "P"-flagged subscriber
        assert result["max_idle_seconds"] == 120
        assert result["address_breakdown"]["10.0.0.1"] == 2
        assert result["command_breakdown"]["blpop"] == 1
        assert result["returned_clients"] == 3
        assert result["clients"][1]["blocked"] is True
        mock_client.close.assert_called_once()

    @patch("app.integrations.redis._get_client")
    def test_sample_capped_but_aggregates_count_all(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.client_list.return_value = [
            {"id": str(i), "addr": f"10.0.0.{i}:5000", "flags": "N", "idle": "0", "cmd": "ping"}
            for i in range(60)
        ]
        mock_get_client.return_value = mock_client

        result = get_client_list(RedisConfig(host="cache", max_results=50))

        assert result["total_clients"] == 60  # all counted
        assert result["returned_clients"] == 50  # sample capped
        assert len(result["address_breakdown"]) == 50  # top-N cap on breakdown

    def test_not_configured(self):
        assert get_client_list(RedisConfig(host=""))["available"] is False


class TestRedisListDepth:
    @patch("app.integrations.redis._get_client")
    def test_list_depth_with_head_and_tail(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.type.return_value = "list"
        mock_client.pipeline.return_value.execute.return_value = [3, ["a", "b"], ["c"]]
        mock_get_client.return_value = mock_client

        result = get_list_depth(RedisConfig(host="cache"), key="jobs", head=2, tail=1)

        assert result["available"] is True
        assert result["type"] == "list"
        assert result["exists"] is True
        assert result["depth"] == 3
        assert result["head"] == ["a", "b"]
        assert result["tail"] == ["c"]
        mock_client.pipeline.assert_called_once_with(transaction=False)
        mock_client.close.assert_called_once()

    @patch("app.integrations.redis._get_client")
    def test_depth_only_when_no_sample_requested(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.type.return_value = "list"
        mock_client.pipeline.return_value.execute.return_value = [42]
        mock_get_client.return_value = mock_client

        result = get_list_depth(RedisConfig(host="cache"), key="jobs")

        assert result["depth"] == 42
        assert result["head"] == []
        assert result["tail"] == []

    @patch("app.integrations.redis._get_client")
    def test_missing_key_reports_not_exists(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.type.return_value = "none"
        mock_get_client.return_value = mock_client

        result = get_list_depth(RedisConfig(host="cache"), key="absent")

        assert result["available"] is True
        assert result["exists"] is False
        assert result["depth"] == 0
        mock_client.pipeline.assert_not_called()  # no LLEN/LRANGE on a missing key

    @patch("app.integrations.redis._get_client")
    def test_wrong_type_returns_clear_message_not_wrongtype_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.type.return_value = "string"
        mock_get_client.return_value = mock_client

        result = get_list_depth(RedisConfig(host="cache"), key="counter")

        assert result["available"] is True
        assert result["type"] == "string"
        assert result["depth"] is None
        assert "not a list" in result["error"]
        mock_client.pipeline.assert_not_called()

    @patch("app.integrations.redis._get_client")
    def test_head_sample_capped_and_values_truncated(self, mock_get_client):
        long_value = "x" * 1000
        mock_client = MagicMock()
        mock_client.type.return_value = "list"
        mock_client.pipeline.return_value.execute.return_value = [1, [long_value]]
        mock_get_client.return_value = mock_client

        result = get_list_depth(RedisConfig(host="cache"), key="jobs", head=999)

        # head clamped to max_results (default 50) when the LRANGE was issued
        _, start, end = mock_client.pipeline.return_value.lrange.call_args.args
        assert (start, end) == (0, 49)
        # each value truncated to the preview cap
        assert result["head"][0].endswith("…")
        assert len(result["head"][0]) <= 257

    def test_empty_key_rejected(self):
        result = get_list_depth(RedisConfig(host="cache"), key="  ")
        assert result["available"] is False
        assert "required" in result["error"]


class TestRedisLatencyDoctor:
    @patch("app.integrations.redis._get_client")
    def test_report_latest_and_history(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.execute_command.return_value = "Dave, I found spikes in command."
        mock_client.latency_latest.return_value = [["command", 1700000000, 250, 900]]
        mock_client.latency_history.return_value = [[1700000000, 250], [1700000060, 300]]
        mock_get_client.return_value = mock_client

        result = get_latency_doctor(RedisConfig(host="cache"), event="command")

        assert result["available"] is True
        assert result["monitoring_active"] is True
        assert result["monitored_events"] == 1
        assert result["latest"][0]["event"] == "command"
        assert result["latest"][0]["max_ms"] == 900
        assert result["history"] == [
            {"timestamp": 1700000000, "latency_ms": 250},
            {"timestamp": 1700000060, "latency_ms": 300},
        ]
        mock_client.execute_command.assert_called_once_with("LATENCY", "DOCTOR")
        mock_client.close.assert_called_once()

    @patch("app.integrations.redis._get_client")
    def test_no_events_when_monitoring_disabled(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.execute_command.return_value = "Latency monitoring is disabled."
        mock_client.latency_latest.return_value = []
        mock_get_client.return_value = mock_client

        result = get_latency_doctor(RedisConfig(host="cache"))

        assert result["available"] is True
        assert result["monitoring_active"] is False
        assert result["latest"] == []
        assert result["history"] == []
        mock_client.latency_history.assert_not_called()  # no event requested

    @patch("app.integrations.redis._get_client")
    def test_history_capped_at_max_results(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.execute_command.return_value = "report"
        mock_client.latency_latest.return_value = []
        mock_client.latency_history.return_value = [[i, i] for i in range(100)]
        mock_get_client.return_value = mock_client

        result = get_latency_doctor(RedisConfig(host="cache", max_results=50), event="command")
        assert len(result["history"]) == 50


class TestNewToolErrorHandling:
    @patch("app.integrations.redis.report_validation_failure")
    @patch("app.integrations.redis._get_client")
    def test_client_list_auth_error_is_graceful_without_sentry(self, mock_get_client, mock_report):
        import redis.exceptions as redis_exc

        mock_client = MagicMock()
        mock_client.client_list.side_effect = redis_exc.AuthenticationError("WRONGPASS")
        mock_get_client.return_value = mock_client

        result = get_client_list(RedisConfig(host="cache"))
        assert result["available"] is False
        assert "authentication" in result["error"].lower()
        mock_report.assert_not_called()

    @patch("app.integrations.redis.report_validation_failure")
    @patch("app.integrations.redis._get_client")
    def test_latency_other_error_reports_sentry(self, mock_get_client, mock_report):
        mock_client = MagicMock()
        mock_client.execute_command.side_effect = Exception("connection reset")
        mock_get_client.return_value = mock_client

        result = get_latency_doctor(RedisConfig(host="cache"))
        assert result["available"] is False
        assert "connection reset" in result["error"]
        mock_report.assert_called_once()


class TestResolveIntegrations:
    def test_classify_redis(self):
        integrations = [
            {
                "id": "123",
                "service": "redis",
                "status": "active",
                "credentials": {
                    "host": "cache.example.net",
                    "port": 6380,
                    "password": "secret",
                    "db": 1,
                },
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "redis" in resolved
        assert resolved["redis"]["host"] == "cache.example.net"
        assert resolved["redis"]["port"] == 6380
        assert resolved["redis"]["db"] == 1
        assert resolved["redis"]["ssl"] is False  # default
        assert resolved["redis"]["integration_id"] == "123"
        assert "timeout_seconds" not in resolved["redis"]
        assert "max_results" not in resolved["redis"]
