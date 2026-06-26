"""PagerDuty integration verifier."""

from __future__ import annotations

from integrations.pagerduty.client import PagerDutyClient, PagerDutyConfig
from integrations.verification import register_probe_verifier

verify_pagerduty = register_probe_verifier(
    "pagerduty",
    config=PagerDutyConfig.model_validate,
    client=PagerDutyClient,
)
