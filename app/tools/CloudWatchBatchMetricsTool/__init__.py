"""CloudWatch metrics for AWS Batch jobs."""

from __future__ import annotations

from typing import Any

from app.services.cloudwatch_client import get_metric_statistics
from app.tools.tool_decorator import tool
from app.tools.utils.compaction import truncate_list

_BATCH_JOB_QUEUE_KEYS = (
    "job_queue",
    "jobQueue",
    "batch_job_queue",
    "batchJobQueue",
    "aws_batch_job_queue",
    "awsBatchJobQueue",
)
_BATCH_METRIC_TYPE_KEYS = ("metric_type", "metricType", "batch_metric_type", "batchMetricType")
_BATCH_LIMIT_KEYS = (
    "limit",
    "metric_limit",
    "metricLimit",
    "batch_metric_limit",
    "batchMetricLimit",
)
_BATCH_SOURCE_KEYS = ("cloudwatch", "aws_metadata")


def _first_source_value(sources: dict[str, dict], keys: tuple[str, ...]) -> str:
    for source_name in _BATCH_SOURCE_KEYS:
        source = sources.get(source_name, {})
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized:
                return normalized
    return ""


def _coerce_limit(value: str, default: int = 50) -> int:
    try:
        limit = int(value)
    except ValueError:
        return default
    return limit if limit > 0 else default


def _cloudwatch_batch_metrics_available(sources: dict[str, dict]) -> bool:
    return bool(_first_source_value(sources, _BATCH_JOB_QUEUE_KEYS))


def _cloudwatch_batch_metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "job_queue": _first_source_value(sources, _BATCH_JOB_QUEUE_KEYS),
    }
    metric_type = _first_source_value(sources, _BATCH_METRIC_TYPE_KEYS)
    if metric_type:
        params["metric_type"] = metric_type
    limit = _first_source_value(sources, _BATCH_LIMIT_KEYS)
    if limit:
        params["limit"] = _coerce_limit(limit)
    return params


@tool(
    name="get_cloudwatch_batch_metrics",
    source="cloudwatch",
    description="Get CloudWatch metrics for AWS Batch jobs.",
    use_cases=[
        "Proving resource constraint hypothesis",
        "Understanding batch job performance",
        "Identifying AWS infrastructure issues",
    ],
    tags=("metrics", "aws"),
    cost_tier="moderate",
    requires=["job_queue"],
    is_available=_cloudwatch_batch_metrics_available,
    extract_params=_cloudwatch_batch_metrics_extract_params,
    input_schema={
        "type": "object",
        "properties": {
            "job_queue": {"type": "string", "description": "The AWS Batch job queue name"},
            "metric_type": {"type": "string", "enum": ["cpu", "memory"], "default": "cpu"},
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of metric data points to return",
            },
        },
        "required": ["job_queue"],
    },
)
def get_cloudwatch_batch_metrics(
    job_queue: str, metric_type: str = "cpu", limit: int = 50
) -> dict[str, Any]:
    """Get CloudWatch metrics for AWS Batch jobs."""
    if not job_queue:
        return {"error": "job_queue is required"}
    if metric_type not in ["cpu", "memory"]:
        return {"error": "metric_type must be 'cpu' or 'memory'"}

    try:
        metric_name = "CPUUtilization" if metric_type == "cpu" else "MemoryUtilization"
        metrics_response = get_metric_statistics(
            namespace="AWS/Batch",
            metric_name=metric_name,
            dimensions=[{"Name": "JobQueue", "Value": job_queue}],
            statistics=["Average", "Maximum"],
        )

        # Handle the response structure - extract datapoints if present
        if isinstance(metrics_response, dict) and metrics_response.get("success"):
            datapoints = metrics_response.get("data", {}).get("Datapoints", [])
            # Truncate datapoints to stay within prompt limits
            compacted_datapoints = truncate_list(datapoints, limit=limit, default_limit=limit)
            return {
                "metrics": {"Datapoints": compacted_datapoints},
                "metric_type": metric_type,
                "job_queue": job_queue,
                "source": "AWS CloudWatch API",
                "total_datapoints": len(datapoints),
            }
        elif isinstance(metrics_response, list):
            # Handle mocked/test responses that return a list directly
            compacted_metrics = truncate_list(metrics_response, limit=limit, default_limit=limit)
            return {
                "metrics": compacted_metrics,
                "metric_type": metric_type,
                "job_queue": job_queue,
                "source": "AWS CloudWatch API",
                "total_metrics": len(metrics_response),
            }
        else:
            # Error case
            return {
                "error": metrics_response.get("error", "Unknown error"),
                "job_queue": job_queue,
            }
    except Exception as e:
        return {"error": f"CloudWatch not available: {str(e)}"}
