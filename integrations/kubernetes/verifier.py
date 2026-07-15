"""Kubernetes integration verifier.

Registered with the central plugin registry at import time. The loader
at ``integrations/_verifiers_loader.py`` is the single place that
imports this module to trigger the registration.
"""

from __future__ import annotations

from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.client import KubernetesClient
from integrations.verification import register_probe_verifier

verify_kubernetes = register_probe_verifier(
    "kubernetes",
    config=KubernetesIntegrationConfig.model_validate,
    client=KubernetesClient,
)
