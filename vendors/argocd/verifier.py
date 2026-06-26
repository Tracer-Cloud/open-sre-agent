"""Argo CD integration verifier."""

from __future__ import annotations

from integrations.verification import register_probe_verifier
from vendors.argocd.client import ArgoCDClient, ArgoCDConfig

verify_argocd = register_probe_verifier(
    "argocd",
    config=ArgoCDConfig.model_validate,
    client=ArgoCDClient,
)
