"""Tests for the uvicorn probe access-log filter (gateway/web/access_log.py)."""

from __future__ import annotations

import logging
from http import HTTPStatus

from gateway.web.access_log import ProbeAccessLogFilter, install_probe_access_log_filter


def _access_record(path: str, status: int) -> logging.LogRecord:
    """Build a record shaped like uvicorn's access log line."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("10.0.0.1:53124", "GET", path, "1.1", status),
        exc_info=None,
    )


def test_successful_probe_hits_are_dropped() -> None:
    probe_filter = ProbeAccessLogFilter()
    for path in ("/health", "/healthz", "/readyz", "/ok"):
        assert probe_filter.filter(_access_record(path, HTTPStatus.OK)) is False


def test_failing_probe_still_logs() -> None:
    probe_filter = ProbeAccessLogFilter()
    record = _access_record("/readyz", HTTPStatus.SERVICE_UNAVAILABLE)
    assert probe_filter.filter(record) is True


def test_real_routes_are_untouched() -> None:
    probe_filter = ProbeAccessLogFilter()
    for path in ("/", "/alerts", "/investigate", "/healthcheck"):
        assert probe_filter.filter(_access_record(path, HTTPStatus.OK)) is True


def test_query_string_does_not_defeat_the_match() -> None:
    probe_filter = ProbeAccessLogFilter()
    assert probe_filter.filter(_access_record("/health?verbose=1", HTTPStatus.OK)) is False


def test_non_access_shaped_records_pass_through() -> None:
    probe_filter = ProbeAccessLogFilter()
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Application startup complete.",
        args=None,
        exc_info=None,
    )
    assert probe_filter.filter(record) is True


def test_install_is_idempotent() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    original = list(access_logger.filters)
    try:
        access_logger.filters = []
        install_probe_access_log_filter()
        install_probe_access_log_filter()
        installed = [f for f in access_logger.filters if isinstance(f, ProbeAccessLogFilter)]
        assert len(installed) == 1
    finally:
        access_logger.filters = original
