"""Ordered status rows shared by the CLI and shell gateway commands."""

from __future__ import annotations

from gateway.core.process import GATEWAY_LOG_FILE
from surfaces.shared.gateway_status import gateway_status_lines


def test_running_daemon_reports_pid() -> None:
    lines = gateway_status_lines(pid=4242, components={})

    assert lines.daemon == ("OpenSRE gateway", "running (pid 4242)")


def test_no_pid_reports_stopped() -> None:
    lines = gateway_status_lines(pid=None, components={})

    assert lines.daemon == ("OpenSRE gateway", "stopped")


def test_components_preserve_order() -> None:
    lines = gateway_status_lines(
        pid=None, components={"web": "serving :8000", "telegram": "polling"}
    )

    assert lines.components == [("web", "serving :8000"), ("telegram", "polling")]


def test_logs_row_uses_the_shared_log_path() -> None:
    lines = gateway_status_lines(pid=None, components={})

    assert lines.logs == ("Logs", str(GATEWAY_LOG_FILE))
