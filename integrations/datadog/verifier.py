"""Datadog integration verifier."""

from __future__ import annotations

from integrations.datadog.client import DatadogClient, DatadogConfig
from integrations.verification import register_probe_verifier

verify_datadog = register_probe_verifier(
    "datadog",
    config=DatadogConfig.model_validate,
    client=DatadogClient,
)
