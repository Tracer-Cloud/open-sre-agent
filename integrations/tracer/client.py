"""Unified Tracer API client composed from mixins."""

from integrations.tracer.aws_batch_jobs import AWSBatchJobsMixin
from integrations.tracer.tracer_integrations import TracerIntegrationsMixin
from integrations.tracer.tracer_logs import TracerLogsMixin
from integrations.tracer.tracer_pipelines import TracerPipelinesMixin
from integrations.tracer.tracer_tools import TracerToolsMixin


class TracerClient(
    TracerPipelinesMixin,
    TracerToolsMixin,
    AWSBatchJobsMixin,
    TracerLogsMixin,
    TracerIntegrationsMixin,
):
    """Unified HTTP client for Tracer API (staging and web app)."""

    pass
