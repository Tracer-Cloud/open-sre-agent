"""Wire integrations-layer helpers into :mod:`platform.harness_ports`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    import integrations.webapp_vault as webapp_vault
    from integrations.catalog import (
        classify_integrations,
        configured_integration_services,
        load_env_integrations,
        merge_integrations_by_service,
        merge_local_integrations,
    )
    from integrations.store import STORE_PATH, load_integrations
    from platform.harness_ports import set_integration_resolution_adapters

    set_integration_resolution_adapters(
        load_integrations=load_integrations,
        integration_store_path=lambda: str(STORE_PATH),
        load_env_integrations=load_env_integrations,
        classify_integrations=classify_integrations,
        merge_local_integrations=merge_local_integrations,
        merge_integrations_by_service=merge_integrations_by_service,
        configured_services=lambda: tuple(configured_integration_services()),
        fetch_webapp_vault=lambda: webapp_vault.fetch_webapp_org_integrations(),
    )

    _register_vcs_repo_scope_providers()
    _register_cli_llm_adapters()
    _register_alert_source_detectors()
    _register_alert_source_routing()
    _register_incident_anchor_parsers()
    _register_prompt_fragments()
    _register_taxonomy_profiles()
    _register_message_context_strippers()
    _register_alert_detail_fields()
    _register_secondary_tool_sources()
    _register_gateway_persona()


def _register_vcs_repo_scope_providers() -> None:
    from integrations.github.repo_scope import GITHUB_VCS_REPO_SCOPE_PROVIDER
    from integrations.gitlab.repo_scope import GITLAB_VCS_REPO_SCOPE_PROVIDER
    from platform.harness_ports import (
        clear_vcs_repo_scope_providers,
        register_vcs_repo_scope_provider,
    )

    clear_vcs_repo_scope_providers()
    register_vcs_repo_scope_provider(GITHUB_VCS_REPO_SCOPE_PROVIDER)
    register_vcs_repo_scope_provider(GITLAB_VCS_REPO_SCOPE_PROVIDER)


def _register_alert_source_detectors() -> None:
    from core.domain.alerts.alert_source import (
        clear_alert_source_detectors,
        register_alert_source_detector,
    )
    from integrations.grafana.alert_source_detect import detect_grafana_alert_source

    clear_alert_source_detectors()
    register_alert_source_detector(detect_grafana_alert_source)


def _register_alert_source_routing() -> None:
    from core.domain.alerts.alert_source import clear_alert_source_routing, clear_source_aliases
    from integrations.alert_source_catalog import register_all_alert_source_routing

    clear_alert_source_routing()
    clear_source_aliases()
    register_all_alert_source_routing()


def _register_incident_anchor_parsers() -> None:
    from core.domain.types.incident_anchors import (
        clear_anchor_parsers,
        register_anchor_parser,
    )
    from integrations.alertmanager.incident_anchor import alertmanager_incident_anchor
    from integrations.aws.cloudwatch_incident_anchor import cloudwatch_incident_anchor
    from integrations.datadog.incident_anchor import datadog_incident_anchor
    from integrations.pagerduty.incident_anchor import pagerduty_incident_anchor

    clear_anchor_parsers()
    # Order matters: the first parser to find an anchor wins. The order
    # reflects which format expresses incident-start most accurately.
    register_anchor_parser(alertmanager_incident_anchor)
    register_anchor_parser(pagerduty_incident_anchor)
    register_anchor_parser(datadog_incident_anchor)
    register_anchor_parser(cloudwatch_incident_anchor)


def _register_prompt_fragments() -> None:
    from integrations.github.action_prompt import github_action_prompt_fragment
    from integrations.github.gather_prompt import github_gather_prompt_fragment
    from integrations.posthog.assistant_prompt import posthog_assistant_prompt_fragment
    from integrations.rocketchat.action_prompt import rocketchat_action_prompt_fragment
    from integrations.sentry.assistant_prompt import sentry_assistant_prompt_fragment
    from integrations.sentry.gather_prompt import sentry_gather_prompt_fragment
    from integrations.slack.action_prompt import slack_action_prompt_fragment
    from integrations.slack.assistant_prompt import slack_assistant_prompt_fragment
    from integrations.slack.gather_prompt import slack_gather_prompt_fragment
    from integrations.telegram.action_prompt import telegram_action_prompt_fragment
    from platform.harness_ports import (
        clear_action_prompt_fragments,
        clear_assistant_prompt_fragments,
        clear_gather_prompt_fragments,
        register_action_prompt_fragment,
        register_assistant_prompt_fragment,
        register_gather_prompt_fragment,
    )

    clear_gather_prompt_fragments()
    register_gather_prompt_fragment(github_gather_prompt_fragment)
    register_gather_prompt_fragment(sentry_gather_prompt_fragment)
    register_gather_prompt_fragment(slack_gather_prompt_fragment)

    clear_action_prompt_fragments()
    register_action_prompt_fragment(slack_action_prompt_fragment)
    register_action_prompt_fragment(github_action_prompt_fragment)
    register_action_prompt_fragment(telegram_action_prompt_fragment)
    register_action_prompt_fragment(rocketchat_action_prompt_fragment)

    clear_assistant_prompt_fragments()
    register_assistant_prompt_fragment(sentry_assistant_prompt_fragment)
    register_assistant_prompt_fragment(posthog_assistant_prompt_fragment)
    register_assistant_prompt_fragment(slack_assistant_prompt_fragment)


def _register_taxonomy_profiles() -> None:
    from core.domain.diagnosis.taxonomy_registry import (
        clear_taxonomy_profiles,
        register_taxonomy_profile,
    )
    from integrations.hermes.taxonomy import HERMES_TAXONOMY_PROFILE

    clear_taxonomy_profiles()
    register_taxonomy_profile(HERMES_TAXONOMY_PROFILE)


def _register_message_context_strippers() -> None:
    from integrations.slack.message_context import strip_slack_context_prefix
    from platform.harness_ports import (
        clear_message_context_prefix_strippers,
        register_message_context_prefix_stripper,
    )

    clear_message_context_prefix_strippers()
    register_message_context_prefix_stripper(strip_slack_context_prefix)


def _register_alert_detail_fields() -> None:
    from core.domain.alerts.extraction import (
        clear_alert_detail_fields,
        register_alert_detail_fields,
    )
    from integrations.aws.alert_detail_fields import ALERT_DETAIL_FIELDS

    clear_alert_detail_fields()
    register_alert_detail_fields(*ALERT_DETAIL_FIELDS)


def _register_secondary_tool_sources() -> None:
    from core.domain.alerts.alert_source import (
        clear_secondary_tool_sources,
        register_secondary_tool_source,
    )

    clear_secondary_tool_sources()
    # Generic fallback sources: useful, but never primary when incident-specific
    # integrations match. Each is owned by its own integration package;
    # registered here (rather than from each package's own module-import time)
    # so the set is explicit and easy to audit in one place.
    for source in ("knowledge", "openclaw", "google_docs"):
        register_secondary_tool_source(source)


def _register_gateway_persona() -> None:
    from integrations.slack.gateway_persona import gateway_persona_prompt_fragment
    from platform.harness_ports import (
        clear_gateway_persona_fragments,
        register_gateway_persona_fragment,
    )

    clear_gateway_persona_fragments()
    register_gateway_persona_fragment(gateway_persona_prompt_fragment)


def _register_cli_llm_adapters() -> None:
    from typing import Any

    from integrations.llm_cli.registry import get_cli_provider_registration
    from integrations.llm_cli.runner import CLIBackedLLMClient
    from integrations.llm_cli.text import flatten_messages_to_prompt
    from platform.harness_ports import set_cli_llm_adapters

    def _build_cli_client(
        adapter: Any,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        model_type: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"model": model}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if model_type is not None:
            kwargs["model_type"] = model_type
        return CLIBackedLLMClient(adapter, **kwargs)

    set_cli_llm_adapters(
        cli_provider_registration=get_cli_provider_registration,
        build_cli_client=_build_cli_client,
        flatten_cli_messages=flatten_messages_to_prompt,
    )
