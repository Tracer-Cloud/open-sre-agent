"""Tests for the local_process_introspect tool (Phase 5: incident response)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.agents.probe import ProcessSnapshot
from app.agents.tail import AttachUnsupported
from app.tools.LocalProcessIntrospectTool import local_process_introspect
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool
from app.tools.registry import get_registered_tool_map
from tests.tools.conftest import BaseToolContract

_MODULE = "app.tools.LocalProcessIntrospectTool"


def _registered() -> RegisteredTool:
    r = getattr(local_process_introspect, REGISTERED_TOOL_ATTR, None)
    assert isinstance(r, RegisteredTool)
    return r


def _snapshot(pid: int = 4242) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        cpu_percent=4.2,
        rss_mb=128.5,
        num_fds=12,
        num_connections=3,
        status="running",
        started_at=datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC),
        last_output_at=datetime(2026, 6, 4, 9, 5, 0, tzinfo=UTC),
    )


class TestLocalProcessIntrospectContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered()


class TestLocalProcessIntrospectMetadata:
    def test_tool_name(self) -> None:
        assert _registered().name == "local_process_introspect"

    def test_tool_source(self) -> None:
        assert _registered().source == "knowledge"

    def test_pid_is_required_integer(self) -> None:
        schema = _registered().input_schema
        assert "pid" in schema["required"]
        assert schema["properties"]["pid"]["type"] == "integer"

    def test_registered_on_investigation_surface(self) -> None:
        assert "investigation" in _registered().surfaces

    def test_appears_in_registry(self) -> None:
        # Acceptance: the tool shows up in the auto-discovered registry dump.
        assert "local_process_introspect" in get_registered_tool_map()


class TestLocalProcessIntrospectExecution:
    def test_returns_snapshot_and_stdout_tail(self) -> None:
        lines = [f"line {i}" for i in range(50)]
        with (
            patch(f"{_MODULE}.probe", return_value=_snapshot()) as mock_probe,
            patch(f"{_MODULE}.read_tail_lines", return_value=lines) as mock_tail,
        ):
            result = local_process_introspect(pid=4242)

        mock_probe.assert_called_once_with(4242)
        mock_tail.assert_called_once_with(4242, max_lines=50)
        assert result["found"] is True
        assert result["source"] == "knowledge"
        assert result["snapshot"]["pid"] == 4242
        assert result["snapshot"]["cpu_percent"] == 4.2
        # datetimes are serialized to ISO-8601 strings (JSON-safe).
        assert result["snapshot"]["started_at"] == "2026-06-04T09:00:00+00:00"
        assert result["snapshot"]["last_output_at"] == "2026-06-04T09:05:00+00:00"
        assert result["stdout_available"] is True
        assert result["stdout_error"] is None
        assert result["stdout_tail"] == lines
        assert len(result["stdout_tail"]) == 50

    def test_missing_process_returns_not_found(self) -> None:
        with (
            patch(f"{_MODULE}.probe", return_value=None),
            patch(
                f"{_MODULE}.read_tail_lines",
                side_effect=AttachUnsupported("no such pid 9999"),
            ),
        ):
            result = local_process_introspect(pid=9999)

        assert result["found"] is False
        assert result["snapshot"] is None
        assert result["stdout_available"] is False
        assert result["stdout_error"] == "no such pid 9999"
        assert result["stdout_tail"] == []

    def test_untailable_stdout_still_returns_snapshot(self) -> None:
        # A live process whose stdout is a terminal: snapshot succeeds,
        # the tail fails independently without failing the whole call.
        with (
            patch(f"{_MODULE}.probe", return_value=_snapshot()),
            patch(
                f"{_MODULE}.read_tail_lines",
                side_effect=AttachUnsupported("stdout is on a terminal; live tail not supported"),
            ),
        ):
            result = local_process_introspect(pid=4242)

        assert result["found"] is True
        assert result["snapshot"]["pid"] == 4242
        assert result["stdout_available"] is False
        assert result["stdout_error"] == "stdout is on a terminal; live tail not supported"
        assert result["stdout_tail"] == []

    def test_snapshot_nullable_fields_pass_through(self) -> None:
        snap = ProcessSnapshot(
            pid=7,
            cpu_percent=0.0,
            rss_mb=1.0,
            num_fds=None,
            num_connections=None,
            status="sleeping",
            started_at=datetime(2026, 6, 4, 9, 0, 0, tzinfo=UTC),
            last_output_at=None,
        )
        with (
            patch(f"{_MODULE}.probe", return_value=snap),
            patch(f"{_MODULE}.read_tail_lines", return_value=[]),
        ):
            result = local_process_introspect(pid=7)

        assert result["snapshot"]["num_fds"] is None
        assert result["snapshot"]["num_connections"] is None
        assert result["snapshot"]["last_output_at"] is None
