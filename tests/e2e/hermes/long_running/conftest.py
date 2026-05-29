from __future__ import annotations

import os

from app.config import has_credentials_for_active_llm_provider

LLM_CREDENTIAL_SKIP_REASON = (
    "SKIPPED: Hermes long-running e2e requires both active LLM credentials and "
    "OPENSRE_RUN_HERMES_E2E=1"
)


def llm_ready() -> bool:
    return has_credentials_for_active_llm_provider() and os.getenv("OPENSRE_RUN_HERMES_E2E") == "1"
