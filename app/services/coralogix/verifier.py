"""Coralogix integration verifier."""

from __future__ import annotations

from app.integrations.config_models import CoralogixIntegrationConfig
from app.integrations.verification import register_probe_verifier
from app.services.coralogix import CoralogixClient

verify_coralogix = register_probe_verifier(
    "coralogix",
    config=CoralogixIntegrationConfig.model_validate,
    client=CoralogixClient,
)
