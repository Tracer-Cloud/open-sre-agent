"""Tests for the shared service-client error telemetry helper."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services._error_helpers import capture_service_error


@pytest.fixture
def mock_logger() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


def _make_http_status_error(status_code: int = 502) -> httpx.HTTPStatusError:
    response = httpx.Response(status_code, text="Bad Gateway")
    request = httpx.Request("GET", "https://api.example.com/test")
    return httpx.HTTPStatusError("error", request=request, response=response)


class TestCaptureServiceError:
    def test_http_status_error_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(502)
        with patch("app.services._error_helpers.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="jira", method="create_issue"
            )
            mock_report.assert_called_once_with(
                exc,
                logger=mock_logger,
                message="[jira] create_issue failed",
                severity="warning",
                tags={"surface": "service_client", "integration": "jira"},
                extras={"method": "create_issue"},
            )

    def test_generic_exception_uses_error_severity(self, mock_logger: logging.Logger) -> None:
        exc = ConnectionError("refused")
        with patch("app.services._error_helpers.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="datadog", method="search_logs"
            )
            mock_report.assert_called_once_with(
                exc,
                logger=mock_logger,
                message="[datadog] search_logs failed",
                severity="error",
                tags={"surface": "service_client", "integration": "datadog"},
                extras={"method": "search_logs"},
            )

    def test_timeout_exception_uses_error_severity(self, mock_logger: logging.Logger) -> None:
        exc = httpx.ReadTimeout("timed out")
        with patch("app.services._error_helpers.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="splunk", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "error"

    def test_tags_contain_surface_and_integration(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("app.services._error_helpers.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="honeycomb", method="run_query"
            )
            tags = mock_report.call_args.kwargs["tags"]
            assert tags == {"surface": "service_client", "integration": "honeycomb"}

    def test_extras_contain_method(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("app.services._error_helpers.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="vercel", method="get_runtime_logs"
            )
            extras = mock_report.call_args.kwargs["extras"]
            assert extras == {"method": "get_runtime_logs"}
