"""Reading the log a managed database keeps for itself.

Cloud Logging holds what was sent to it. A managed cluster sends nothing there
unless the operator switched export on, so `read_yc_logs` coming back empty says
"not in Cloud Logging" and nothing more — which reads exactly like "there are no
logs" and ends an investigation on a database that has been logging all along.

The engine's own endpoint is the other half. It has a trap of its own: each
engine keeps several streams and serves one at a time, so asking about slow
queries and getting the error log back looks like an answer.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.mdb_catalog import ENGINES, resolve_engine
from tools.registry import get_registered_tool_map

_CREDENTIALS: dict[str, Any] = {"folder_id": "b1gfolder", "iam_token": "t1.token"}


def _responder(payload: dict[str, Any], captured: dict[str, Any] | None = None) -> Any:
    """Return a request stub that records what was asked for."""

    def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        if captured is not None:
            captured["url"] = url
            captured["params"] = kwargs.get("params") or {}
        return httpx.Response(HTTPStatus.OK, json=payload)

    return _request


def _read(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any] | None = None, **kwargs: Any):
    monkeypatch.setattr(
        "integrations.yandex_cloud.rest_client.send_request",
        _responder({"logs": [{"message": {"message": "connection refused"}}]}, captured),
    )
    tool = get_registered_tool_map()["read_yc_db_logs"]
    return tool.run(**{**_CREDENTIALS, **kwargs})


class TestItIsRegistered:
    def test_the_tool_exists(self) -> None:
        assert "read_yc_db_logs" in get_registered_tool_map("action")

    def test_it_says_it_is_a_separate_store_from_cloud_logging(self) -> None:
        """Otherwise there is no reason for the agent to try it after an empty read."""
        description = get_registered_tool_map()["read_yc_db_logs"].description

        assert "separate store from Cloud Logging" in description

    def test_credentials_are_hidden_from_the_model(self) -> None:
        tool = get_registered_tool_map()["read_yc_db_logs"]

        assert "iam_token" not in tool.input_schema.get("properties", {})
        assert "iam_token" in tool.injected_params


class TestWhichEndpointItReads:
    def test_it_asks_the_owning_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        _read(monkeypatch, captured, engine="postgresql", cluster_id="c9q7abc")

        assert "/managed-postgresql/v1/clusters/c9q7abc:logs" in captured["url"]

    def test_former_product_names_still_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redis is Valkey now, and people still type Redis."""
        captured: dict[str, Any] = {}

        _read(monkeypatch, captured, engine="redis", cluster_id="c9qabc")

        assert "/managed-redis/v1/clusters/c9qabc:logs" in captured["url"]

    def test_an_unknown_engine_lists_the_ones_that_exist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _read(monkeypatch, engine="oracle", cluster_id="c9qabc")

        assert result["available"] is False
        assert "postgresql" in result["error"]


class TestChoosingTheLogStream:
    def test_the_engines_primary_stream_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        result = _read(monkeypatch, captured, engine="postgresql", cluster_id="c9qabc")

        assert captured["params"]["serviceType"] == "POSTGRESQL"
        assert result["service_type"] == "POSTGRESQL"

    def test_mysql_defaults_to_the_error_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not the slow-query log — worth knowing before concluding it is empty."""
        captured: dict[str, Any] = {}

        _read(monkeypatch, captured, engine="mysql", cluster_id="c9qabc")

        assert captured["params"]["serviceType"] == "MYSQL_ERROR"

    def test_a_named_stream_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        _read(
            monkeypatch,
            captured,
            engine="mysql",
            cluster_id="c9qabc",
            service_type="mysql_slow_query",
        )

        assert captured["params"]["serviceType"] == "MYSQL_SLOW_QUERY"

    def test_an_unknown_stream_names_the_real_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _read(monkeypatch, engine="postgresql", cluster_id="c9qabc", service_type="NOPE")

        assert result["available"] is False
        assert "POOLER" in result["error"]

    def test_the_alternatives_come_back_with_the_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A result that names the streams it did not read is a result you can act on."""
        result = _read(monkeypatch, engine="postgresql", cluster_id="c9qabc")

        assert result["available_service_types"] == ["POOLER", "REPACK"]

    def test_an_engine_without_streams_sends_no_such_parameter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kafka has one log and rejects the parameter."""
        captured: dict[str, Any] = {}

        _read(monkeypatch, captured, engine="kafka", cluster_id="c9qabc")

        assert "serviceType" not in captured["params"]


class TestTheWindowAndFilter:
    def test_an_explicit_window_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A past incident needs one; the default window ends now."""
        captured: dict[str, Any] = {}

        _read(
            monkeypatch,
            captured,
            engine="postgresql",
            cluster_id="c9qabc",
            from_time="2026-08-01T00:00:00Z",
            to_time="2026-08-02T00:00:00Z",
        )

        assert captured["params"]["fromTime"] == "2026-08-01T00:00:00Z"
        assert captured["params"]["toTime"] == "2026-08-02T00:00:00Z"

    def test_no_window_sends_no_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        _read(monkeypatch, captured, engine="postgresql", cluster_id="c9qabc")

        assert "fromTime" not in captured["params"]

    def test_a_filter_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        _read(
            monkeypatch,
            captured,
            engine="postgresql",
            cluster_id="c9qabc",
            filter="message.hostname='rc1a-abc'",
        )

        assert captured["params"]["filter"] == "message.hostname='rc1a-abc'"


class TestEveryEngineCanBeAsked:
    @pytest.mark.parametrize("engine", [engine.key for engine in ENGINES])
    def test_the_path_is_reachable(self, engine: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """A managed engine with no log path would be a silent gap."""
        captured: dict[str, Any] = {}

        result = _read(monkeypatch, captured, engine=engine, cluster_id="c9qabc")

        assert result["available"] is True
        assert captured["url"].endswith(":logs")

    @pytest.mark.parametrize("engine", [engine.key for engine in ENGINES])
    def test_the_declared_streams_match_the_endpoint_index(self, engine: str) -> None:
        """The values come from Yandex's own protobufs, so they can be checked."""
        import json

        from integrations.yandex_cloud.api_index import _INDEX_FILE

        resolved = resolve_engine(engine)
        assert resolved is not None
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]
        endpoint = next(
            entry
            for entry in entries
            if entry["service"] == resolved.service and entry["path"].endswith(":logs")
        )
        declared = next(
            (param for param in endpoint.get("params", []) if param["name"] == "serviceType"),
            None,
        )

        expected = tuple(declared["values"]) if declared else ()
        assert resolved.log_service_types == expected


class TestAnEmptyCloudLoggingReadSaysWhereElseToLook:
    """The moment the wrong conclusion is drawn, so the moment to say otherwise."""

    def _read_cloud_logging(self, monkeypatch: pytest.MonkeyPatch, entries: list[Any]) -> Any:
        def _entries(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"success": True, "entries": entries, "next_page_token": ""}

        monkeypatch.setattr(
            "integrations.yandex_cloud.logging_client.YandexLoggingClient.read_entries",
            _entries,
        )
        tool = get_registered_tool_map()["read_yc_logs"]
        return tool.run(log_group_id="e23abc", **_CREDENTIALS)

    def test_an_empty_read_points_at_the_other_stores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._read_cloud_logging(monkeypatch, [])

        guidance = result["where_else_logs_live"]
        assert "read_yc_db_logs" in guidance
        assert "kubernetes_get_pod_logs" in guidance

    def test_it_says_which_source_an_empty_result_is_meaningful_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Serverless does log here by default, so there the absence is evidence."""
        result = self._read_cloud_logging(monkeypatch, [])

        assert "Serverless" in result["where_else_logs_live"]

    def test_a_read_that_found_something_says_nothing_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._read_cloud_logging(monkeypatch, [{"message": "hello"}])

        assert "where_else_logs_live" not in result
