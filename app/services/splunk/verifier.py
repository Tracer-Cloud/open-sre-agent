"""Splunk integration verifier."""

from __future__ import annotations

from app.integrations.verification import register_probe_verifier
from app.services.splunk import SplunkClient, SplunkConfig

verify_splunk = register_probe_verifier(
    "splunk",
    config=SplunkConfig.model_validate,
    client=SplunkClient,
)
