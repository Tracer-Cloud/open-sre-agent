"""Tests for app.integrations._validation_helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.integrations._validation_helpers import report_validation_failure


def _mock_logger() -> MagicMock:
    return MagicMock(spec=logging.Logger)


class TestReportValidationFailure:
    def test_default_severity_is_warning(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("app.utils.errors.capture_exception"):
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="trello",
                method="validate_trello_config",
            )
        mock_log.warning.assert_called_once()
        mock_log.error.assert_not_called()

    def test_message_includes_integration_and_method(self) -> None:
        mock_log = _mock_logger()
        with patch("app.utils.errors.capture_exception"):
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="kafka",
                method="get_topic_health",
            )
        message = mock_log.warning.call_args[0][1]
        assert message == "[kafka] get_topic_health validation failed"

    def test_tags_have_expected_shape(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="postgresql",
                method="get_server_status",
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["tag.surface"] == "integration"
        assert extra["tag.integration"] == "postgresql"
        assert extra["tag.event"] == "validation_failed"
        assert extra["tag.method"] == "get_server_status"

    def test_extras_pass_through_unprefixed(self) -> None:
        mock_log = _mock_logger()
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="airflow",
                method="get_recent_airflow_failures.task_instances",
                extras={"dag_id": "dag-42", "dag_run_id": "run-7"},
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["dag_id"] == "dag-42"
        assert extra["dag_run_id"] == "run-7"
        # extras should NOT be prefixed with "tag." (they're not Sentry tags)
        assert "tag.dag_id" not in extra
        assert "tag.dag_run_id" not in extra

    def test_severity_override_routes_to_logger(self) -> None:
        mock_log = _mock_logger()
        with patch("app.utils.errors.capture_exception"):
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="mongodb",
                method="get_server_status",
                severity="error",
            )
        mock_log.error.assert_called_once()
        mock_log.warning.assert_not_called()

    def test_captures_to_sentry_exactly_once(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("once")
        with patch("app.utils.errors.capture_exception") as mock_cap:
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="mysql",
                method="validate_mysql_config",
            )
        mock_cap.assert_called_once()
        assert mock_cap.call_args[0][0] is exc
