"""Helm integration verifier."""

from __future__ import annotations

from integrations.config_models import HelmIntegrationConfig
from integrations.helm.client import HelmClient
from integrations.verification import register_probe_verifier

verify_helm = register_probe_verifier(
    "helm",
    config=HelmIntegrationConfig.model_validate,
    client=HelmClient,
)
