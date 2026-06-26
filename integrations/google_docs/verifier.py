"""Google Docs integration verifier."""

from __future__ import annotations

from integrations.config_models import GoogleDocsIntegrationConfig
from integrations.verification import register_probe_verifier
from tools.google_docs_tools import GoogleDocsClient

verify_google_docs = register_probe_verifier(
    "google_docs",
    config=GoogleDocsIntegrationConfig.model_validate,
    client=GoogleDocsClient,
)
