"""Google Docs integration verifier."""

from __future__ import annotations

from integrations.config_models import GoogleDocsIntegrationConfig
from integrations.google_docs.client import GoogleDocsClient
from integrations.verification import register_probe_verifier

verify_google_docs = register_probe_verifier(
    "google_docs",
    config=GoogleDocsIntegrationConfig.model_validate,
    client=GoogleDocsClient,
)
