"""Slack integration verifier.

Single ``VerifierFn``-shaped entry. The user-facing ``--send-slack-test``
flag is plumbed via a private ``_send_slack_test`` key in the config
dict, injected by ``verify.verify_integrations(...)`` before dispatch.
Underscore prefix marks it as runtime-only — never read from on-disk
config.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.integrations.config_models import SlackWebhookConfig
from app.integrations.verification import register_verifier, result

RUNTIME_SEND_TEST_KEY = "_send_slack_test"


@register_verifier("slack")
def verify_slack(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        slack_config = SlackWebhookConfig.model_validate(
            {k: v for k, v in config.items() if k != RUNTIME_SEND_TEST_KEY}
        )
    except Exception as err:
        return result("slack", source, "missing", str(err))

    webhook_url = slack_config.webhook_url
    if not webhook_url:
        return result("slack", source, "missing", "SLACK_WEBHOOK_URL is not configured.")

    if not config.get(RUNTIME_SEND_TEST_KEY):
        return result(
            "slack", source, "passed", "Configured. Use --send-slack-test to validate delivery."
        )

    payload = {"text": "Tracer integration test: Slack webhook is configured correctly."}
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
    except Exception as exc:
        return result("slack", source, "failed", f"Webhook delivery failed: {exc}")
    return result("slack", source, "passed", "Webhook delivered test message successfully.")
