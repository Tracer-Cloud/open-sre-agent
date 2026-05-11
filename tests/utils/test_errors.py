"""Tests for app.utils.errors helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.utils.errors import (
    OpenSRESilentFallback,
    report_and_reraise,
    report_and_swallow,
    report_exception,
)


@pytest.fixture(autouse=True)
def _disable_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")


def _mock_logger() -> MagicMock:
    return MagicMock(spec=logging.Logger)


def _raise(exc: BaseException) -> None:
    raise exc


class TestReportException:
    def test_logs_and_captures(self) -> None:
        mock_log = _mock_logger()
        exc = ValueError("test error")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_exception(exc, logger=mock_log, message="Something failed")
        mock_log.error.assert_called_once()
        mock_cap.assert_called_once_with(exc, extra=None)

    def test_warning_severity(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("warn")
        with patch("app.utils.errors.capture_exception"):
            report_exception(exc, logger=mock_log, message="m", severity="warning")
        mock_log.warning.assert_called_once()
        mock_log.error.assert_not_called()

    def test_tags_are_prefixed(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_exception(
                exc,
                logger=mock_log,
                message="msg",
                tags={"surface": "cli", "component": "app.cli"},
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["tag.surface"] == "cli"
        assert extra["tag.component"] == "app.cli"

    def test_extras_are_merged(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_exception(
                exc,
                logger=mock_log,
                message="msg",
                tags={"surface": "tool"},
                extras={"detail": "x"},
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["tag.surface"] == "tool"
        assert extra["detail"] == "x"

    def test_no_tags_or_extras_passes_none(self) -> None:
        mock_log = _mock_logger()
        exc = ValueError("plain")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_exception(exc, logger=mock_log, message="msg")
        mock_cap.assert_called_once_with(exc, extra=None)


class TestReportAndSwallow:
    def test_swallows_matching_exception(self) -> None:
        mock_log = _mock_logger()
        with (
            patch("app.utils.errors.capture_exception"),
            report_and_swallow(logger=mock_log, message="swallowed"),
        ):
            raise ValueError("silent")

    def test_does_not_swallow_other_types(self) -> None:
        mock_log = _mock_logger()
        with (
            pytest.raises(TypeError),
            report_and_swallow(logger=mock_log, message="only value", swallow=ValueError),
        ):
            raise TypeError("not swallowed")

    def test_calls_report_exception_on_match(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("reported")
        with (
            patch("app.utils.errors.capture_exception") as mock_cap,
            report_and_swallow(logger=mock_log, message="msg"),
        ):
            _raise(exc)
        mock_cap.assert_called_once_with(exc, extra=None)

    def test_no_exception_passes_through(self) -> None:
        mock_log = _mock_logger()
        result: list[int] = []
        with (
            patch("app.utils.errors.capture_exception") as mock_cap,
            report_and_swallow(logger=mock_log, message="msg"),
        ):
            result.append(1)
        assert result == [1]
        mock_cap.assert_not_called()

    def test_tuple_of_swallow_types(self) -> None:
        mock_log = _mock_logger()
        for exc_type in (ValueError, KeyError):
            with (
                patch("app.utils.errors.capture_exception"),
                report_and_swallow(
                    logger=mock_log,
                    message="multi",
                    swallow=(ValueError, KeyError),
                ),
            ):
                raise exc_type("x")


class TestReportAndReraise:
    def test_propagates_exception(self) -> None:
        mock_log = _mock_logger()
        with (
            patch("app.utils.errors.capture_exception") as mock_cap,
            pytest.raises(RuntimeError, match="propagated"),
            report_and_reraise(logger=mock_log, message="reraised"),
        ):
            _raise(RuntimeError("propagated"))
        mock_cap.assert_called_once()

    def test_no_exception_passes_through(self) -> None:
        mock_log = _mock_logger()
        result: list[int] = []
        with (
            patch("app.utils.errors.capture_exception") as mock_cap,
            report_and_reraise(logger=mock_log, message="no error"),
        ):
            result.append(42)
        assert result == [42]
        mock_cap.assert_not_called()

    def test_logs_before_reraising(self) -> None:
        mock_log = _mock_logger()
        with (
            patch("app.utils.errors.capture_exception"),
            pytest.raises(ValueError),
            report_and_reraise(logger=mock_log, message="logged"),
        ):
            _raise(ValueError("x"))
        mock_log.error.assert_called_once()


class TestOpenSRESilentFallback:
    def test_is_a_warning(self) -> None:
        assert issubclass(OpenSRESilentFallback, Warning)
