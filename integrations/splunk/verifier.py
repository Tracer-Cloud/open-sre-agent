"""Splunk integration verifier."""

from __future__ import annotations

from integrations.splunk.client import SplunkClient, SplunkConfig
from integrations.verification import register_probe_verifier

verify_splunk = register_probe_verifier(
    "splunk",
    config=SplunkConfig.model_validate,
    client=SplunkClient,
)
