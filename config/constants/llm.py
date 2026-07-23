"""LLM provider connection env-var names.

Kept in this leaf module (not ``config.config``) because modules that
``config.config`` imports — e.g. ``config.llm_auth.provider_catalog`` — also
need them, so co-locating with ``config.config`` would be a cyclic import.
"""

from __future__ import annotations

from typing import Final

AZURE_OPENAI_BASE_URL_ENV: Final[str] = "AZURE_OPENAI_BASE_URL"
AZURE_OPENAI_API_VERSION_ENV: Final[str] = "AZURE_OPENAI_API_VERSION"
AZURE_OPENAI_API_KEY_ENV: Final[str] = "AZURE_OPENAI_API_KEY"
