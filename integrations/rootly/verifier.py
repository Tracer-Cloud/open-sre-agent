"""Rootly integration verifier."""

from __future__ import annotations

from integrations.config_models import RootlyIntegrationConfig
from integrations.rootly.client import RootlyClient
from integrations.verification import register_probe_verifier

verify_rootly = register_probe_verifier(
    "rootly",
    config=RootlyIntegrationConfig.model_validate,
    client=RootlyClient,
)
