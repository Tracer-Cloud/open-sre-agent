"""PostHog integration: env-configured analytics (config, client, verifier)."""

from __future__ import annotations

from integrations.posthog.client import (
    BounceRateAlert,
    BounceRateResult,
    check_bounce_rate_alert,
    query_bounce_rate,
)
from integrations.posthog.config import (
    PostHogConfig,
    build_posthog_config,
    posthog_config_from_env,
)
from integrations.posthog.verifier import (
    PostHogValidationResult,
    validate_posthog_config,
)

__all__ = [
    "BounceRateAlert",
    "BounceRateResult",
    "PostHogConfig",
    "PostHogValidationResult",
    "build_posthog_config",
    "check_bounce_rate_alert",
    "posthog_config_from_env",
    "query_bounce_rate",
    "validate_posthog_config",
]
