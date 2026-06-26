"""Splunk integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from vendors.splunk.client import SplunkClient, SplunkConfig

verify_splunk = register_probe_verifier(
    "splunk",
    config=SplunkConfig.model_validate,
    client=SplunkClient,
)
