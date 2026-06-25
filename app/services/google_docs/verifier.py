"""Google Docs integration verifier."""

from __future__ import annotations

from app.integrations.config_models import GoogleDocsIntegrationConfig
from app.integrations.verification import register_probe_verifier
from app.services.google_docs import GoogleDocsClient

verify_google_docs = register_probe_verifier(
    "google_docs",
    config=GoogleDocsIntegrationConfig.model_validate,
    client=GoogleDocsClient,
)
