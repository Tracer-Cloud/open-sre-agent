from __future__ import annotations

from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.client import RailwayClient
from integrations.verification import register_probe_verifier

verify_railway = register_probe_verifier(
    "railway",
    config=RailwayIntegrationConfig.model_validate,
    client=RailwayClient,
)
