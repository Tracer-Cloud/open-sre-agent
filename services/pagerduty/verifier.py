"""PagerDuty integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from services.pagerduty import PagerDutyClient, PagerDutyConfig

verify_pagerduty = register_probe_verifier(
    "pagerduty",
    config=PagerDutyConfig.model_validate,
    client=PagerDutyClient,
)
