"""Helm integration verifier."""

from __future__ import annotations

from app.integrations.config_models import HelmIntegrationConfig
from app.integrations.verification import register_probe_verifier
from app.services.helm import HelmClient

verify_helm = register_probe_verifier(
    "helm",
    config=HelmIntegrationConfig.model_validate,
    client=HelmClient,
)
