"""Each part of the codebase imports vendors through their main module.

Every ``integrations/<vendor>/`` folder should be used through its main module
``integrations.<vendor>``. Below is the list, per top-level folder, of vendor
files still imported directly from outside the vendor (plus any name imported
from a vendor's ``__init__`` that it does not list in ``__all__``). The list can
only get shorter: adding a new direct import fails the test, and an entry that is
no longer imported must be deleted from the list.

``core`` and ``config`` already import nothing internal. Imports between vendors
(one vendor reaching inside another) are handled separately and are not checked
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared.integrations_api import INTEGRATIONS_BORDER

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Vendor files each top-level folder still imports directly, instead of through
#: the vendor's main module ``integrations.<vendor>``.
_ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "config": frozenset(),
    "infrastructure": frozenset(
        {
            "integrations.github.identity",
            "integrations.llm_cli.errors",
        }
    ),
    "gateway": frozenset(
        {
            "integrations.buzz.client",
            "integrations.slack.formatting",
            "integrations.slack.web_client",
            "integrations.telegram.formatting",
        }
    ),
    "bootstrap": frozenset(
        {
            "integrations.buzz.background_adapter",
            "integrations.discord.scheduled_delivery",
            "integrations.llm_cli.auth_check",
            "integrations.rocketchat.background_adapter",
            "integrations.rocketchat.scheduled_delivery",
            "integrations.slack.scheduled_delivery",
            "integrations.smtp.background_adapter",
            "integrations.telegram.background_adapter",
            "integrations.telegram.scheduled_delivery",
        }
    ),
    "tools": frozenset(
        {
            "integrations.buzz.alarms",
            "integrations.buzz.credentials",
            "integrations.buzz.reporting_adapter",
            "integrations.datadog.correlation.registration",
            "integrations.discord.reporting_adapter",
            "integrations.github.client",
            "integrations.github.helpers",
            "integrations.github.pull_requests",
            "integrations.gitlab.build_gitlab_config",
            "integrations.gitlab.post_gitlab_mr_note",
            "integrations.grafana.reporting_adapter",
            "integrations.llm_cli.claude_code",
            "integrations.llm_cli.subprocess_env",
            "integrations.openclaw.reporting_adapter",
            "integrations.opensre.hf_remote",
            "integrations.opensre.llm_eval_judge",
            "integrations.rocketchat.alarms",
            "integrations.rocketchat.credentials",
            "integrations.rocketchat.reporting_adapter",
            "integrations.sentry.SentryConfig",
            "integrations.sentry.get_sentry_issue",
            "integrations.sentry.issue_url",
            "integrations.sentry.sentry_config_from_env",
            "integrations.slack.reporting_adapter",
            "integrations.telegram.alarms",
            "integrations.telegram.credentials",
            "integrations.telegram.formatting",
            "integrations.telegram.reporting_adapter",
            "integrations.twilio.reporting_adapter",
            "integrations.whatsapp.reporting_adapter",
        }
    ),
    "surfaces": frozenset(
        {
            "integrations.aws.role_mode_gate",
            "integrations.aws.setup",
            "integrations.betterstack.setup",
            "integrations.buzz.alarms",
            "integrations.buzz.client",
            "integrations.buzz.credentials",
            "integrations.buzz.setup",
            "integrations.dagster.setup",
            "integrations.datadog.setup",
            "integrations.discord.setup",
            "integrations.github.identity",
            "integrations.github.login",
            "integrations.github.mcp",
            "integrations.github.mcp_oauth",
            "integrations.github.setup",
            "integrations.gitlab.setup",
            "integrations.grafana.client",
            "integrations.grafana.setup",
            "integrations.hermes.agent",
            "integrations.hermes.correlating_sink",
            "integrations.hermes.correlator",
            "integrations.hermes.investigation",
            "integrations.hermes.sinks",
            "integrations.jenkins.setup",
            "integrations.llm_cli.antigravity_cli",
            "integrations.llm_cli.base",
            "integrations.llm_cli.binary_resolver",
            "integrations.llm_cli.claude_code",
            "integrations.llm_cli.codex",
            "integrations.llm_cli.codex_oauth",
            "integrations.llm_cli.copilot",
            "integrations.llm_cli.cursor",
            "integrations.llm_cli.errors",
            "integrations.llm_cli.gemini_cli",
            "integrations.llm_cli.grok_cli",
            "integrations.llm_cli.kimi",
            "integrations.llm_cli.opencode",
            "integrations.llm_cli.pi_cli",
            "integrations.openclaw.build_openclaw_config",
            "integrations.openclaw.setup",
            "integrations.openclaw.validate_openclaw_config",
            "integrations.posthog.report_prerequisites",
            "integrations.posthog.setup",
            "integrations.posthog_mcp.build_posthog_mcp_config",
            "integrations.posthog_mcp.setup",
            "integrations.posthog_mcp.validate_posthog_mcp_config",
            "integrations.rocketchat.alarms",
            "integrations.rocketchat.credentials",
            "integrations.rocketchat.setup",
            "integrations.sentry.digest_prerequisites",
            "integrations.sentry.get_sentry_auth_recommendations",
            "integrations.sentry.setup",
            "integrations.sentry.uptime",
            "integrations.sentry_mcp.build_sentry_mcp_config",
            "integrations.sentry_mcp.setup",
            "integrations.sentry_mcp.validate_sentry_mcp_config",
            "integrations.servicenow.setup",
            "integrations.servicenow.verifier",
            "integrations.slack.setup",
            "integrations.telegram.alarms",
            "integrations.telegram.credentials",
            "integrations.telegram.setup",
            "integrations.tempo.setup",
            "integrations.tracer.integrations_adapter",
        }
    ),
}


@pytest.mark.parametrize("consumer", sorted(_ALLOWED))
def test_consumer_vendor_imports_match_the_allowlist(consumer: str) -> None:
    # Arrange / Act
    imported = INTEGRATIONS_BORDER.internal_imports_under(
        REPO_ROOT / consumer, exclude_parts=frozenset({"tests"})
    )

    # Assert
    INTEGRATIONS_BORDER.assert_matches_allowlist(
        imported, _ALLOWED[consumer], consumer=f"{consumer}/"
    )
