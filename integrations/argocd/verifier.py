"""Argo CD integration verifier."""

from __future__ import annotations

from integrations.argocd.client import ArgoCDClient, ArgoCDConfig
from integrations.verification import register_probe_verifier

verify_argocd = register_probe_verifier(
    "argocd",
    config=ArgoCDConfig.model_validate,
    client=ArgoCDClient,
)
