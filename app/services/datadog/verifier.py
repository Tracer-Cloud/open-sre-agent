"""Datadog integration verifier."""

from __future__ import annotations

from app.integrations.verification import register_probe_verifier
from app.services.datadog.client import DatadogClient, DatadogConfig

verify_datadog = register_probe_verifier(
    "datadog",
    config=DatadogConfig.model_validate,
    client=DatadogClient,
)
