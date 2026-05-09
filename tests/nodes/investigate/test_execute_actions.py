"""Tests for execute_actions error reporting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.nodes.investigate.execution.execute_actions import (
    _execute_with_retry,
    execute_actions,
)


def _make_action(*, run_fn, extract_params_fn=None, is_available_fn=None, name="test_action"):
    """Build a minimal action stub matching the RegisteredTool interface."""
    action = SimpleNamespace(
        name=name,
        run=run_fn,
        extract_params=extract_params_fn or (lambda _sources: {}),
        is_available=is_available_fn or (lambda _sources: True),
    )
    return action


class TestRetryBreadcrumbOnTransientRecovery:
    """Transient failure that recovers on retry."""

    @patch("app.nodes.investigate.execution.execute_actions.time.sleep")
    @patch("app.nodes.investigate.execution.execute_actions.report_exception")
    @patch("sentry_sdk.add_breadcrumb")
    def test_breadcrumb_emitted_no_sentry_event(self, mock_breadcrumb, mock_report, _mock_sleep):
        call_count = 0

        def flaky_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("connection reset")
            return {"result": "ok"}

        action = _make_action(run_fn=flaky_run)
        result = _execute_with_retry("my_action", action, {}, max_attempts=3)

        assert result.success is True
        mock_breadcrumb.assert_called_once()
        breadcrumb_kwargs = mock_breadcrumb.call_args
        assert breadcrumb_kwargs[1]["data"]["action_name"] == "my_action"
        assert breadcrumb_kwargs[1]["data"]["attempt"] == 1
        assert breadcrumb_kwargs[1]["data"]["transient"] is True
        mock_report.assert_not_called()


class TestRetryExhaustionReportsToSentry:
    """All retries exhausted."""

    @patch("app.nodes.investigate.execution.execute_actions.time.sleep")
    @patch("app.nodes.investigate.execution.execute_actions.report_exception")
    @patch("sentry_sdk.add_breadcrumb")
    def test_transient_failure_reports_warning(self, mock_breadcrumb, mock_report, _mock_sleep):
        def always_timeout(**kwargs):
            raise TimeoutError("timeout")

        action = _make_action(run_fn=always_timeout)
        result = _execute_with_retry("my_action", action, {}, max_attempts=3)

        assert result.success is False
        assert mock_breadcrumb.call_count == 2
        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args
        assert call_kwargs[1]["severity"] == "warning"
        assert call_kwargs[1]["extras"]["attempts"] == 3
        assert call_kwargs[1]["tags"]["surface"] == "node"

    @patch("app.nodes.investigate.execution.execute_actions.time.sleep")
    @patch("app.nodes.investigate.execution.execute_actions.report_exception")
    @patch("sentry_sdk.add_breadcrumb")
    def test_nontransient_failure_reports_error(self, mock_breadcrumb, mock_report, _mock_sleep):
        def always_value_error(**kwargs):
            raise ValueError("bad input")

        action = _make_action(run_fn=always_value_error)
        result = _execute_with_retry("my_action", action, {}, max_attempts=3)

        assert result.success is False
        mock_breadcrumb.assert_not_called()
        mock_report.assert_called_once()
        assert mock_report.call_args[1]["severity"] == "error"
        assert mock_report.call_args[1]["extras"]["attempts"] == 1


class TestFutureFailureReportsToSentry:
    """Future raises outside the retry loop."""

    @patch("app.nodes.investigate.execution.execute_actions.report_exception")
    @patch("sentry_sdk.add_breadcrumb")
    def test_future_exception_captured(self, _mock_breadcrumb, mock_report):
        action = _make_action(run_fn=lambda **_: {}, name="boom_action")

        with patch(
            "app.nodes.investigate.execution.execute_actions._execute_single_action",
            side_effect=RuntimeError("executor blew up"),
        ):
            results = execute_actions(
                action_names=["boom_action"],
                available_actions={"boom_action": action},
                available_sources={},
            )

        assert results["boom_action"].success is False
        assert "executor blew up" in results["boom_action"].error
        mock_report.assert_called_once()
        call_kwargs = mock_report.call_args
        assert call_kwargs[1]["tags"]["surface"] == "node"
        assert call_kwargs[1]["extras"]["action_name"] == "boom_action"
