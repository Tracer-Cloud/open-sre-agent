import os

from config.config import has_credentials_for_active_llm_provider

LLM_CREDENTIAL_SKIP_REASON = (
    "Hermes e2e tests require OPENSRE_RUN_HERMES_E2E=1 "
    "and usable LLM configuration for the active provider."
)


def llm_ready() -> bool:
    return has_credentials_for_active_llm_provider() and os.getenv("OPENSRE_RUN_HERMES_E2E") == "1"
