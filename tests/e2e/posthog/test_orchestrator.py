from __future__ import annotations

import os

import pytest

from integrations.posthog import posthog_config_from_env, validate_posthog_config

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTHOG_PERSONAL_API_KEY") or not os.getenv("POSTHOG_PROJECT_ID"),
    reason="PostHog env vars not set — skipping E2E",
)


def test_posthog_verify_e2e() -> None:
    """E2E: PostHog REST credentials validate against the live project API."""

    config = posthog_config_from_env()
    assert config is not None, "PostHog config should be loaded from env"

    result = validate_posthog_config(config)
    assert result.ok is True
    assert result.detail == "PostHog validated."
