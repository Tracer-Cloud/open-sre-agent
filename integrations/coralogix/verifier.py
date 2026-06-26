"""Coralogix integration verifier."""

from __future__ import annotations

from integrations.config_models import CoralogixIntegrationConfig
from integrations.coralogix.client import CoralogixClient
from integrations.verification import register_probe_verifier

verify_coralogix = register_probe_verifier(
    "coralogix",
    config=CoralogixIntegrationConfig.model_validate,
    client=CoralogixClient,
)
