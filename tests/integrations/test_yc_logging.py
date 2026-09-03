"""Yandex Cloud Logging: filters, entry reads and the optional gRPC stubs.

Reading log entries is the one Yandex Cloud read with no REST endpoint, so it
goes over gRPC with generated stubs that ship as an optional extra. Listing log
groups is plain REST and works without them, which is why the failure path has
to say so rather than read as a broken integration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from integrations.yandex_cloud.logging_client import (
    MAX_FILTER_LENGTH,
    LogReadingUnavailableError,
    retention_warning,
    validate_filter,
)
from integrations.yandex_cloud.logging_client import resolve_window as resolve_log_window
from integrations.yandex_cloud.tools import list_yc_log_groups, read_yc_logs

FOLDER = "b1gexamplefolder"
_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.yandex_cloud.endpoints._fetch_endpoints", dict)
    from integrations.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


class TestRegistration:
    @pytest.mark.parametrize("name", ["read_yc_logs", "list_yc_log_groups"])
    def test_the_tool_is_discoverable(self, name: str) -> None:
        from tools.registry import get_registered_tool_map

        assert name in get_registered_tool_map("action")


class TestLogFilters:
    def test_an_over_long_filter_is_refused_with_advice(self) -> None:
        rejected = validate_filter("x" * (MAX_FILTER_LENGTH + 1))

        assert rejected is not None
        assert str(MAX_FILTER_LENGTH) in rejected

    def test_a_normal_filter_passes(self) -> None:
        assert validate_filter('level >= WARN AND message: "timeout"') is None

    def test_a_window_beyond_retention_is_flagged(self) -> None:
        note = retention_warning(datetime.now(UTC) - timedelta(days=40))

        assert "retention" in note

    def test_a_recent_window_is_not_flagged(self) -> None:
        assert retention_warning(datetime.now(UTC) - timedelta(hours=2)) == ""

    def test_a_log_window_defaults_to_the_recent_past(self) -> None:
        start, end = resolve_log_window("", "", 15)

        assert end - start == timedelta(minutes=15)


class TestLogReads:
    def test_a_missing_log_group_asks_for_one(self) -> None:
        result = read_yc_logs(log_group_id="", **_CREDENTIALS)

        assert result["available"] is False
        assert "list_yc_log_groups" in result["error"]

    def test_the_missing_grpc_dependency_explains_the_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything else in the integration works without it, so say so."""

        def _raise(*_a: Any, **_k: Any) -> None:
            raise LogReadingUnavailableError(_real_missing_dependency_message())

        monkeypatch.setattr(
            "integrations.yandex_cloud.logging_client.YandexLoggingClient.read_entries", _raise
        )
        result = read_yc_logs(log_group_id="e23abc", **_CREDENTIALS)

        assert result["available"] is False
        assert "pip install" in result["error"]
        assert "log groups and reading metrics work" in result["error"]


def _real_missing_dependency_message() -> str:
    """Return the message the client itself produces when the stubs are absent.

    Taken from the real code path rather than written out here: a hand-copied
    message drifts, and an install hint that names something unavailable is
    exactly the kind of mistake a test should catch rather than enshrine.
    """
    import sys

    from integrations.yandex_cloud import logging_client as client

    original = sys.modules.get("grpc")
    sys.modules["grpc"] = None  # type: ignore[assignment]  # makes the import fail
    try:
        client._load_logging_protos()
    except LogReadingUnavailableError as exc:
        return str(exc)
    finally:
        if original is None:
            sys.modules.pop("grpc", None)
        else:
            sys.modules["grpc"] = original
    raise AssertionError("the stubs imported when they were meant to be missing")


class TestTheGrpcPathWhenTheStubsArePresent:
    """The other half of the optional dependency, exercised where it is installed.

    Everything else about Cloud Logging is covered with the stubs absent, since
    that is the common case and what CI installs. This one runs wherever the
    extra is present, so enabling it locally checks the gRPC path too rather
    than leaving it to the first person who turns it on in production.
    """

    def test_the_stubs_import_and_return_what_the_client_expects(self) -> None:
        pytest.importorskip("yandexcloud", reason="the logs extra is not installed here")

        from integrations.yandex_cloud import logging_client as client

        service_pb2, service_pb2_grpc, entry_pb2 = client._load_logging_protos()

        assert hasattr(service_pb2, "ReadRequest")
        assert hasattr(service_pb2_grpc, "LogReadingServiceStub")
        assert hasattr(entry_pb2, "LogEntry")


class TestTheInstallHintIsRealisable:
    """An install hint naming something that cannot be installed is worse than none.

    Anyone following a hint that names a non-existent extra gets "no matches
    found" and no way forward, so the name in the message is checked against
    the extras this project actually declares.
    """

    def test_it_names_this_package(self) -> None:
        assert "opensre[yandex_cloud_logs]" in _real_missing_dependency_message()

    def test_the_extra_it_names_actually_exists(self) -> None:
        import re
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        message = _real_missing_dependency_message()

        for name, extra in re.findall(r"pip install '([\w-]+)\[(\w+)\]'", message):
            assert name == metadata["name"], name
            assert extra in metadata["optional-dependencies"], extra

    def test_it_says_what_still_works_without_the_stubs(self) -> None:
        """Otherwise a missing optional dependency reads as a broken integration."""
        message = _real_missing_dependency_message()

        assert "log groups" in message
        assert "metrics" in message

    def test_entries_come_back_with_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _read(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {
                "success": True,
                "error": None,
                "entries": [{"uid": "1", "level": "ERROR", "message": "boom"}],
                "next_page_token": "page-2",
            }

        monkeypatch.setattr(
            "integrations.yandex_cloud.logging_client.YandexLoggingClient.read_entries", _read
        )
        result = read_yc_logs(log_group_id="e23abc", levels=["ERROR"], **_CREDENTIALS)

        assert result["available"] is True
        assert result["entry_count"] == 1
        assert result["next_page_token"] == "page-2"
        assert result["window"]["since"] < result["window"]["until"]

    def test_log_groups_are_listed_from_the_management_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Groups are REST on logging.api; only entry reads use the reader host."""
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            return httpx.Response(
                200, json={"groups": [{"id": "e23abc", "name": "default", "status": "ACTIVE"}]}
            )

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = list_yc_log_groups(**_CREDENTIALS)

        assert urlparse(captured["url"]).hostname == "logging.api.cloud.yandex.net"
        assert result["count"] == 1
        assert result["log_groups"][0]["id"] == "e23abc"


class TestGuidanceNamesRealParameters:
    """Advice that names arguments the tool does not take costs the agent a turn."""

    def test_the_empty_result_advice_matches_the_schema(self) -> None:
        from integrations.yandex_cloud.tools.yc_logs_tool import _WHERE_ELSE_LOGS_LIVE
        from tools.registry import get_registered_tool_map

        properties = set(
            get_registered_tool_map("action")["read_yc_logs"].input_schema["properties"]
        )
        named = {
            word.strip(".,'\"")
            for word in _WHERE_ELSE_LOGS_LIVE.split()
            if word.strip(".,'\"") in {"since", "until", "from_time", "to_time", "window_minutes"}
        }

        assert named, "the advice should name the window arguments"
        assert named <= properties, f"names arguments the tool lacks: {named - properties}"
