"""Coralogix integration verifier."""

from __future__ import annotations

from integrations.config_models import CoralogixIntegrationConfig
from integrations.verification import register_probe_verifier
from vendors.coralogix import CoralogixClient

verify_coralogix = register_probe_verifier(
    "coralogix",
    config=CoralogixIntegrationConfig.model_validate,
    client=CoralogixClient,
)
