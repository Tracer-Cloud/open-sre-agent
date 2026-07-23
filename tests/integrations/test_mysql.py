"""Unit tests for the MySQL integration module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.mysql import (
    MySQLConfig,
    MySQLValidationResult,
    build_mysql_config,
    get_current_processes,
    get_replication_status,
    get_server_status,
    get_slow_queries,
    get_table_stats,
    mysql_config_from_env,
)


class TestMySQLConfig:
    """Tests for MySQLConfig model."""

    def test_defaults(self) -> None:
        config = MySQLConfig(host="localhost", database="testdb")
        assert config.host == "localhost"
        assert config.port == 3306
        assert config.database == "testdb"
        assert config.username == "root"
        assert config.password == ""
        assert config.ssl_mode == "preferred"
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_host_and_database(self) -> None:
        config = MySQLConfig(host="mysql.example.com", database="mydb")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = MySQLConfig(database="mydb")
        assert config.is_configured is False

    def test_is_configured_without_database(self) -> None:
        config = MySQLConfig(host="localhost")
        assert config.is_configured is False

    def test_is_configured_without_host_and_database(self) -> None:
        config = MySQLConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = MySQLConfig(host="  mysql.example.com  ", database="mydb")
        assert config.host == "mysql.example.com"

    def test_normalize_empty_host(self) -> None:
        config = MySQLConfig(host="", database="mydb")
        assert config.host == ""
        assert config.is_configured is False

    def test_normalize_database_strips_whitespace(self) -> None:
        config = MySQLConfig(host="localhost", database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_empty_database(self) -> None:
        config = MySQLConfig(host="localhost", database="")
        assert config.database == ""
        assert config.is_configured is False

    def test_normalize_username_default(self) -> None:
        config = MySQLConfig(host="localhost", database="mydb", username="")
        assert config.username == "root"

    def test_normalize_ssl_mode_default(self) -> None:
        config = MySQLConfig(host="localhost", database="mydb", ssl_mode="")
        assert config.ssl_mode == "preferred"

    def test_custom_values(self) -> None:
        config = MySQLConfig(
            host="mysql.prod.internal",
            port=3307,
            database="analytics",
            username="reader",
            password="secret",
            ssl_mode="required",
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.host == "mysql.prod.internal"
        assert config.port == 3307
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.ssl_mode == "required"
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100


class TestBuildMySQLConfig:
    """Tests for build_mysql_config helper."""

    def test_from_dict(self) -> None:
        config = build_mysql_config({"host": "mysql.example.com", "database": "mydb", "port": 3307})
        assert config.host == "mysql.example.com"
        assert config.database == "mydb"
        assert config.port == 3307

    def test_from_none(self) -> None:
        config = build_mysql_config(None)
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_mysql_config({})
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False


class TestMySQLConfigFromEnv:
    """Tests for mysql_config_from_env helper."""

    def test_returns_none_without_host(self, monkeypatch) -> None:
        monkeypatch.delenv("MYSQL_HOST", raising=False)
        monkeypatch.delenv("MYSQL_DATABASE", raising=False)
        result = mysql_config_from_env()
        assert result is None

    def test_returns_none_without_database(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.delenv("MYSQL_DATABASE", raising=False)
        result = mysql_config_from_env()
        assert result is None

    def test_returns_config_with_host_and_database(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "mysql.test.local")
        monkeypatch.setenv("MYSQL_PORT", "3307")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")
        monkeypatch.setenv("MYSQL_USERNAME", "testuser")
        monkeypatch.setenv("MYSQL_PASSWORD", "testpass")
        monkeypatch.setenv("MYSQL_SSL_MODE", "required")
        config = mysql_config_from_env()
        assert config is not None
        assert config.host == "mysql.test.local"
        assert config.port == 3307
        assert config.database == "testdb"
        assert config.username == "testuser"
        assert config.password == "testpass"
        assert config.ssl_mode == "required"

    def test_non_numeric_port_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")
        monkeypatch.setenv("MYSQL_PORT", "abc")
        config = mysql_config_from_env()
        assert config is not None
        assert config.port == 3306


class TestMySQLValidationResult:
    """Tests for MySQLValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = MySQLValidationResult(
            ok=True, detail="Connected to MySQL 8.0.32; target database: mydb."
        )
        assert result.ok is True
        assert result.detail == "Connected to MySQL 8.0.32; target database: mydb."

    def test_error_result(self) -> None:
        result = MySQLValidationResult(
            ok=False, detail="MySQL connection failed: connection refused"
        )
        assert result.ok is False
        assert result.detail == "MySQL connection failed: connection refused"


class _FakeCursor:
    """DictCursor stand-in returning queued results in order.

    ``results`` feeds ``fetchall``, ``rows`` feeds ``fetchone``. Statements whose
    text starts with any prefix in ``unsupported`` raise the same
    ``ProgrammingError`` a real server raises for an unknown statement, which is
    how the replication version fallback is exercised.
    """

    def __init__(
        self,
        results: list[list[dict[str, Any]]] | None = None,
        *,
        rows: list[dict[str, Any] | None] | None = None,
        unsupported: tuple[str, ...] = (),
    ) -> None:
        self._results = list(results or [])
        self._rows = list(rows or [])
        self._unsupported = unsupported
        self.statements: list[tuple[str, Any]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    def execute(self, statement: str, params: Any = None) -> None:
        self.statements.append((statement, params))
        if any(statement.strip().startswith(prefix) for prefix in self._unsupported):
            import pymysql

            raise pymysql.err.ProgrammingError(1064, f"unsupported statement: {statement}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._results.pop(0) if self._results else []


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _configured() -> MySQLConfig:
    return build_mysql_config({"host": "mysql.test.local", "database": "testdb"})


class TestDiagnosticsUnconfigured:
    """Diagnostics short-circuit before connecting when host/database are missing."""

    def test_server_status_unavailable(self) -> None:
        result = get_server_status(build_mysql_config({}))
        assert result["available"] is False
        assert result["error"] == "Not configured."
        assert result["source"] == "mysql"

    def test_table_stats_unavailable(self) -> None:
        result = get_table_stats(build_mysql_config({"host": "mysql.test.local"}))
        assert result["available"] is False
        assert result["error"] == "Not configured."


class TestGetTableStats:
    """``get_table_stats`` shapes information_schema.TABLES rows."""

    def test_happy_path(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "TABLE_NAME": "orders",
                        "ENGINE": "InnoDB",
                        "TABLE_ROWS": 1200,
                        "data_mb": "3.5",
                        "index_mb": "1.25",
                        "total_mb": "4.75",
                        "AUTO_INCREMENT": 1201,
                        "TABLE_COLLATION": "utf8mb4_general_ci",
                        "CREATE_TIME": None,
                        "UPDATE_TIME": None,
                    }
                ]
            ]
        )
        conn = _FakeConnection(cursor)

        with patch("pymysql.connect", return_value=conn):
            result = get_table_stats(_configured())

        assert result["available"] is True
        assert result["database"] == "testdb"
        assert result["total_tables"] == 1
        table = result["tables"][0]
        assert table["table_name"] == "orders"
        assert table["engine"] == "InnoDB"
        assert table["row_count_estimate"] == 1200
        assert table["size"] == {"data_mb": 3.5, "index_mb": 1.25, "total_mb": 4.75}
        assert table["created_at"] is None
        assert conn.closed is True

    def test_connection_failure_returns_unavailable(self) -> None:
        with (
            patch("pymysql.connect", side_effect=OSError("connection refused")),
            patch("integrations._relational.report_validation_failure"),
        ):
            result = get_table_stats(_configured())

        assert result["available"] is False
        assert result["error"] == "connection refused"


class TestGetServerStatus:
    """``get_server_status`` derives connection and InnoDB metrics."""

    def test_computes_buffer_pool_hit_ratio(self) -> None:
        status_rows = [
            {"Variable_name": "Threads_connected", "Value": "25"},
            {"Variable_name": "Threads_running", "Value": "5"},
            {"Variable_name": "Uptime", "Value": "432000"},
            {"Variable_name": "Questions", "Value": "1000"},
            {"Variable_name": "Slow_queries", "Value": "42"},
            {"Variable_name": "Innodb_buffer_pool_reads", "Value": "10"},
            {"Variable_name": "Innodb_buffer_pool_read_requests", "Value": "1000"},
        ]
        variable_rows = [
            {"Variable_name": "version", "Value": "8.0.32"},
            {"Variable_name": "max_connections", "Value": "151"},
        ]
        conn = _FakeConnection(_FakeCursor([status_rows, variable_rows]))

        with patch("pymysql.connect", return_value=conn):
            result = get_server_status(_configured())

        assert result["available"] is True
        assert result["version"] == "8.0.32"
        assert result["uptime_seconds"] == 432000
        assert result["connections"]["current"] == 25
        assert result["connections"]["max"] == 151
        assert result["queries"]["slow"] == 42
        # 1 - (10 / 1000) = 99.0%
        assert result["innodb"]["buffer_pool_hit_ratio_percent"] == 99.0
        assert conn.closed is True

    def test_zero_read_requests_avoids_division_by_zero(self) -> None:
        status_rows = [{"Variable_name": "Innodb_buffer_pool_read_requests", "Value": "0"}]
        conn = _FakeConnection(_FakeCursor([status_rows, []]))

        with patch("pymysql.connect", return_value=conn):
            result = get_server_status(_configured())

        assert result["innodb"]["buffer_pool_hit_ratio_percent"] == 0.0


class TestGetCurrentProcesses:
    """``get_current_processes`` shapes PROCESSLIST rows and honours the threshold."""

    def test_happy_path(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "ID": 42,
                        "USER": "app",
                        "HOST": "10.0.0.7:51000",
                        "DB": "testdb",
                        "COMMAND": "Query",
                        "TIME": 12,
                        "STATE": "Sending data",
                        "INFO": "SELECT * FROM orders",
                    }
                ]
            ]
        )
        conn = _FakeConnection(cursor)

        with patch("pymysql.connect", return_value=conn):
            result = get_current_processes(_configured(), threshold_seconds=5)

        assert result["available"] is True
        assert result["threshold_seconds"] == 5
        assert result["total_processes"] == 1
        proc = result["processes"][0]
        assert proc["id"] == 42
        assert proc["user"] == "app"
        assert proc["database"] == "testdb"
        assert proc["time_seconds"] == 12
        assert proc["query"] == "SELECT * FROM orders"
        # threshold and the result cap are bound as query params, not interpolated
        assert cursor.statements[0][1] == (5, 50)

    def test_null_columns_become_empty_strings(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "ID": 1,
                        "USER": "root",
                        "HOST": None,
                        "DB": None,
                        "COMMAND": "Query",
                        "TIME": None,
                        "STATE": None,
                        "INFO": None,
                    }
                ]
            ]
        )

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_current_processes(_configured())

        proc = result["processes"][0]
        assert proc["host"] == ""
        assert proc["database"] == ""
        assert proc["state"] == ""
        assert proc["query"] == ""
        assert proc["time_seconds"] == 0

    def test_no_processes(self) -> None:
        with patch("pymysql.connect", return_value=_FakeConnection(_FakeCursor([[]]))):
            result = get_current_processes(_configured())

        assert result["total_processes"] == 0
        assert result["processes"] == []


class TestGetSlowQueries:
    """``get_slow_queries`` depends on performance_schema being enabled."""

    def test_reports_when_performance_schema_disabled(self) -> None:
        cursor = _FakeCursor(rows=[{"@@performance_schema": 0}])

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_slow_queries(_configured())

        assert result["available"] is True
        assert result["performance_schema_available"] is False
        assert result["queries"] == []
        assert "performance_schema is disabled" in result["note"]
        # It must not go on to query the digest table.
        assert len(cursor.statements) == 1

    def test_happy_path(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "DIGEST_TEXT": "SELECT * FROM orders WHERE id = ?",
                        "SCHEMA_NAME": "testdb",
                        "COUNT_STAR": 17,
                        "avg_time_ms": "2.500",
                        "total_time_ms": "42.500",
                        "min_time_ms": "1.000",
                        "max_time_ms": "9.000",
                        "SUM_ROWS_EXAMINED": 340,
                        "SUM_ROWS_SENT": 17,
                        "SUM_NO_INDEX_USED": 1,
                        "SUM_NO_GOOD_INDEX_USED": 0,
                    }
                ]
            ],
            rows=[{"@@performance_schema": 1}],
        )
        conn = _FakeConnection(cursor)

        with patch("pymysql.connect", return_value=conn):
            result = get_slow_queries(_configured(), threshold_ms=1000.0)

        assert result["performance_schema_available"] is True
        assert result["threshold_ms"] == 1000.0
        assert result["total_queries"] == 1
        query = result["queries"][0]
        assert query["digest_text"] == "SELECT * FROM orders WHERE id = ?"
        assert query["count"] == 17
        assert query["avg_time_ms"] == 2.5
        assert query["rows_examined"] == 340
        assert query["no_index_used"] == 1
        # milliseconds are converted to the picosecond timer unit
        assert cursor.statements[1][1] == (1_000_000_000_000, 50)
        assert conn.closed is True

    def test_null_timings_default_to_zero(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "DIGEST_TEXT": None,
                        "SCHEMA_NAME": None,
                        "COUNT_STAR": None,
                        "avg_time_ms": None,
                        "total_time_ms": None,
                        "min_time_ms": None,
                        "max_time_ms": None,
                        "SUM_ROWS_EXAMINED": None,
                        "SUM_ROWS_SENT": None,
                        "SUM_NO_INDEX_USED": None,
                        "SUM_NO_GOOD_INDEX_USED": None,
                    }
                ]
            ],
            rows=[{"@@performance_schema": 1}],
        )

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_slow_queries(_configured())

        query = result["queries"][0]
        assert query["digest_text"] == ""
        assert query["avg_time_ms"] == 0.0
        assert query["max_time_ms"] == 0.0
        assert query["rows_examined"] == 0


class TestGetReplicationStatus:
    """``get_replication_status`` handles both the modern and legacy statements."""

    def test_not_a_replica(self) -> None:
        cursor = _FakeCursor([[]])

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_replication_status(_configured())

        assert result["available"] is True
        assert result["replicas"] == []
        assert result["note"] == "This server is not configured as a replica."

    def test_uses_modern_statement_when_supported(self) -> None:
        cursor = _FakeCursor(
            [
                [
                    {
                        "Replica_IO_Running": "Yes",
                        "Replica_SQL_Running": "Yes",
                        "Seconds_Behind_Source": 3,
                        "Source_Host": "primary.internal",
                        "Unrelated_Column": "dropped",
                    }
                ]
            ]
        )

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_replication_status(_configured())

        assert result["replica_count"] == 1
        replica = result["replicas"][0]
        assert replica["Replica_IO_Running"] == "Yes"
        assert replica["Seconds_Behind_Source"] == 3
        # only curated keys survive
        assert "Unrelated_Column" not in replica
        assert cursor.statements[0][0] == "SHOW REPLICA STATUS"

    def test_falls_back_to_legacy_statement(self) -> None:
        """MySQL < 8.0.22 rejects SHOW REPLICA STATUS; SHOW SLAVE STATUS is tried next."""
        cursor = _FakeCursor(
            [
                [
                    {
                        "Slave_IO_Running": "Yes",
                        "Slave_SQL_Running": "No",
                        "Seconds_Behind_Master": 120,
                        "Last_Errno": 1236,
                    }
                ]
            ],
            unsupported=("SHOW REPLICA STATUS",),
        )

        with patch("pymysql.connect", return_value=_FakeConnection(cursor)):
            result = get_replication_status(_configured())

        assert [stmt for stmt, _ in cursor.statements] == [
            "SHOW REPLICA STATUS",
            "SHOW SLAVE STATUS",
        ]
        assert result["replica_count"] == 1
        replica = result["replicas"][0]
        assert replica["Slave_SQL_Running"] == "No"
        assert replica["Seconds_Behind_Master"] == 120

    def test_non_programming_errors_are_not_swallowed(self) -> None:
        """A real failure must surface as unavailable, not be mistaken for a fallback."""
        conn = _FakeConnection(_FakeCursor([[]]))
        conn._cursor.execute = MagicMock(side_effect=RuntimeError("server gone"))  # type: ignore[method-assign]

        with (
            patch("pymysql.connect", return_value=conn),
            patch("integrations._relational.report_validation_failure"),
        ):
            result = get_replication_status(_configured())

        assert result["available"] is False
        assert result["error"] == "server gone"
