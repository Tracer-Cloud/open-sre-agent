"""Tests for ``env_overrides`` helpers shared by CLI LLM adapters."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.integrations.llm_cli import env_overrides


def test_nonempty_env_values_skips_empty_and_missing() -> None:
    with patch.dict(
        os.environ,
        {"FOO_API_KEY": "  sk-secret  ", "BAR_API_KEY": "", "OTHER": "x"},
        clear=False,
    ):
        got = env_overrides.nonempty_env_values(("FOO_API_KEY", "BAR_API_KEY", "MISSING"))
    assert got == {"FOO_API_KEY": "sk-secret"}


def test_http_llm_provider_keys_include_openai_platform_subset() -> None:
    """Codex subset must remain embedded in multi-provider OpenCode forwarding list."""
    for key in env_overrides.OPENAI_PLATFORM_ENV_KEYS:
        assert key in env_overrides.HTTP_LLM_PROVIDER_ENV_KEYS


def test_openai_platform_keys_stable_tuple() -> None:
    assert env_overrides.OPENAI_PLATFORM_ENV_KEYS[:4] == (
        "OPENAI_API_KEY",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENAI_BASE_URL",
    )
