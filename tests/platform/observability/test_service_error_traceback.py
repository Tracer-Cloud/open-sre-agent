"""A failing health probe must not dump a stack into the user's terminal.

An unreachable cluster or a stopped local service is an *expected* outcome of a
probe, not a fault. Logging it with ``exc_info`` put ~100 lines of urllib3 stack
into the interactive shell in place of a one-line status, which is what a user
sees the first time they ask a harmless question with a service stopped.

Genuine call failures must keep their traceback, so these tests pin the
distinction rather than "probes are quiet".
"""

from __future__ import annotations

import io
import logging

from platform.observability.errors.service import capture_service_error


def _raised_connection_error() -> ConnectionRefusedError:
    """A real raised exception — one constructed inline carries no traceback."""
    try:
        raise ConnectionRefusedError(61, "Connection refused")
    except ConnectionRefusedError as exc:
        return exc


def _capture(method: str) -> str:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"test.service_error.{method}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    capture_service_error(
        _raised_connection_error(),
        logger=logger,
        integration="kubernetes",
        method=method,
    )
    return buffer.getvalue()


def test_a_failed_probe_logs_one_line_without_a_stack() -> None:
    """This is what the user reads when their cluster is stopped."""
    # Act
    output = _capture("probe_access")

    # Assert
    assert output.strip() == "[kubernetes] probe_access failed"
    assert "Traceback" not in output


def test_a_real_call_failure_keeps_its_traceback() -> None:
    """Quieting probes must not quiet everything — that would hide real faults."""
    # Act
    output = _capture("list_pods")

    # Assert
    assert "Traceback" in output
    assert "ConnectionRefusedError" in output
