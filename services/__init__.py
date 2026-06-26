"""Client modules for different services."""

from services.cloudwatch_client import get_metric_statistics
from services.coralogix import (
    CoralogixClient,
    build_coralogix_logs_query,
)
from services.grafana import (
    GrafanaAccountConfig,
    GrafanaClient,
    get_grafana_client_from_credentials,
)
from services.honeycomb import HoneycombClient
from services.llm_client import (
    RootCauseResult,
    get_llm_for_reasoning,
    get_llm_for_tools,
    parse_root_cause,
    reset_llm_singletons,
)
from services.s3_client import S3CheckResult, check_s3_marker_presence
from services.tracer_client import (
    AWSBatchJobResult,
    LogResult,
    PipelineRunSummary,
    PipelineSummary,
    TracerClient,
    TracerRunResult,
    TracerTaskResult,
    get_tracer_client,
    get_tracer_web_client,
)

__all__ = [
    # CloudWatch client
    "get_metric_statistics",
    # Coralogix client
    "CoralogixClient",
    "build_coralogix_logs_query",
    # Grafana client
    "GrafanaAccountConfig",
    "GrafanaClient",
    "get_grafana_client_from_credentials",
    # Honeycomb client
    "HoneycombClient",
    # LLM client
    "RootCauseResult",
    "get_llm_for_reasoning",
    "get_llm_for_tools",
    "parse_root_cause",
    "reset_llm_singletons",
    # S3 client
    "S3CheckResult",
    "check_s3_marker_presence",
    # Tracer client
    "AWSBatchJobResult",
    "LogResult",
    "PipelineRunSummary",
    "PipelineSummary",
    "TracerClient",
    "TracerRunResult",
    "TracerTaskResult",
    "get_tracer_client",
    "get_tracer_web_client",
]
