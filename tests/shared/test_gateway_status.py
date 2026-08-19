"""The gateway status content both terminal surfaces render.

`opensre gateway status` and the REPL's `/gateway status` used to build this
list twice and drifted. These tests pin the content and its order in one
place, which is the point of the shared builder.
"""

from __future__ import annotations

from unittest.mock import patch

from surfaces.shared.gateway_status import (
    DAEMON_LABEL,
    LOGS_LABEL,
    GatewayStatus,
    read_gateway_status,
)


def _status(pid: int | None = None, components: tuple[tuple[str, str], ...] = ()) -> GatewayStatus:
    return GatewayStatus(pid=pid, components=components, log_file="/tmp/gateway.log")


class TestDaemonState:
    def test_running_reports_the_pid(self) -> None:
        assert _status(pid=4242).daemon_state == "running (pid 4242)"
        assert _status(pid=4242).running is True

    def test_stopped_when_there_is_no_pid(self) -> None:
        assert _status().daemon_state == "stopped"
        assert _status().running is False


class TestRows:
    def test_daemon_first_then_components_then_logs(self) -> None:
        rows = _status(pid=1, components=(("web", "up"), ("telegram", "down"))).rows()
        assert rows == (
            (DAEMON_LABEL, "running (pid 1)"),
            ("web", "up"),
            ("telegram", "down"),
            (LOGS_LABEL, "/tmp/gateway.log"),
        )

    def test_component_order_is_preserved(self) -> None:
        components = (("c", "3"), ("a", "1"), ("b", "2"))
        assert tuple(r[0] for r in _status(components=components).rows()[1:-1]) == ("c", "a", "b")

    def test_no_components_still_yields_daemon_and_logs(self) -> None:
        rows = _status().rows()
        assert len(rows) == 2
        assert rows[0][0] == DAEMON_LABEL
        assert rows[-1][0] == LOGS_LABEL

    def test_logs_row_is_always_last(self) -> None:
        rows = _status(pid=9, components=(("web", "up"),)).rows()
        assert rows[-1] == (LOGS_LABEL, "/tmp/gateway.log")


class TestReadGatewayStatus:
    def test_reads_pid_and_components_from_the_gateway_process_module(self) -> None:
        with (
            patch("surfaces.shared.gateway_status.gateway_daemon_pid", return_value=77),
            patch(
                "surfaces.shared.gateway_status.read_component_status",
                return_value={"web": "listening on 8080"},
            ),
        ):
            status = read_gateway_status()

        assert status.pid == 77
        assert status.components == (("web", "listening on 8080"),)
        assert status.rows()[0] == (DAEMON_LABEL, "running (pid 77)")

    def test_components_are_a_tuple_so_the_snapshot_cannot_be_mutated(self) -> None:
        with (
            patch("surfaces.shared.gateway_status.gateway_daemon_pid", return_value=None),
            patch(
                "surfaces.shared.gateway_status.read_component_status",
                return_value={"web": "stopped"},
            ),
        ):
            assert isinstance(read_gateway_status().components, tuple)
