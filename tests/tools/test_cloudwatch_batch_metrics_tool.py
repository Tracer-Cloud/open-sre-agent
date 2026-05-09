"""Tests for CloudWatchBatchMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.nodes.investigate.execution.execute_actions import _execute_with_retry
from app.tools.CloudWatchBatchMetricsTool import get_cloudwatch_batch_metrics
from tests.tools.conftest import BaseToolContract


class TestCloudWatchBatchMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_cloudwatch_batch_metrics.__opensre_registered_tool__


def test_is_not_available_without_batch_queue_context() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    assert rt.is_available({}) is False


@pytest.mark.parametrize(
    "queue_key",
    [
        "job_queue",
        "jobQueue",
        "batch_job_queue",
        "batchJobQueue",
        "aws_batch_job_queue",
        "awsBatchJobQueue",
    ],
)
def test_is_available_when_batch_queue_context_exists(queue_key: str) -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    assert rt.is_available({"aws_metadata": {queue_key: "critical-jobs"}}) is True


def test_is_not_available_for_cloudwatch_logs_without_batch_queue() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    assert rt.is_available({"cloudwatch": {"log_group": "/aws/batch/job"}}) is False


def test_extract_params_maps_batch_queue_from_aws_metadata() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "cloudwatch": {"log_group": "/aws/batch/job"},
            "aws_metadata": {
                "batch_job_queue": "critical-jobs",
                "batch_metric_type": "memory",
                "batch_metric_limit": "25",
            },
        }
    )

    assert params == {"job_queue": "critical-jobs", "metric_type": "memory", "limit": 25}


def test_extract_params_returns_empty_job_queue_when_missing() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    assert rt.extract_params({"cloudwatch": {"log_group": "/aws/batch/job"}}) == {"job_queue": ""}


def test_extract_params_uses_default_limit_for_invalid_limit() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    params = rt.extract_params(
        {"aws_metadata": {"batch_job_queue": "critical-jobs", "metric_limit": "not-a-number"}}
    )

    assert params == {"job_queue": "critical-jobs", "limit": 50}


def test_run_returns_error_when_no_job_queue() -> None:
    result = get_cloudwatch_batch_metrics(job_queue="")
    assert "error" in result


def test_run_returns_error_for_invalid_metric_type() -> None:
    result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="invalid")
    assert "error" in result


def test_run_cpu_metrics_happy_path() -> None:
    fake_metrics = [{"Timestamp": "2024-01-01", "Average": 50.0}]
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics", return_value=fake_metrics
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="cpu")
    assert result["metrics"] == fake_metrics
    assert result["metric_type"] == "cpu"
    assert result["job_queue"] == "my-queue"


def test_run_memory_metrics_happy_path() -> None:
    fake_metrics = [{"Timestamp": "2024-01-01", "Average": 80.0}]
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics", return_value=fake_metrics
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="memory")
    assert result["metric_type"] == "memory"


def test_run_handles_exception() -> None:
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics",
        side_effect=Exception("AWS error"),
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue")
    assert "error" in result
    assert "CloudWatch not available" in result["error"]


def test_execute_with_retry_uses_extracted_batch_queue() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics",
        return_value=[{"Timestamp": "2024-01-01", "Average": 50.0}],
    ):
        result = _execute_with_retry(
            "get_cloudwatch_batch_metrics",
            rt,
            {"aws_metadata": {"batch_job_queue": "critical-jobs"}},
            max_attempts=1,
        )

    assert result.success is True
    assert result.error is None
    assert result.data["job_queue"] == "critical-jobs"


def test_execute_with_retry_returns_tool_error_when_batch_queue_missing() -> None:
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    result = _execute_with_retry(
        "get_cloudwatch_batch_metrics",
        rt,
        {"cloudwatch": {"log_group": "/aws/batch/job"}},
        max_attempts=1,
    )

    assert result.success is False
    assert result.error == "job_queue is required"
