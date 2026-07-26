"""Wire integrations-layer helpers into :mod:`platform.harness_ports`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


def register_harness_adapters() -> None:
    import integrations.webapp_vault as webapp_vault
    from integrations.catalog import (
        classify_integrations,
        configured_integration_services,
        load_env_integrations,
        merge_integrations_by_service,
        merge_local_integrations,
    )
    from integrations.github.repo_scope import apply_github_repo_scope, infer_github_repo_scope
    from integrations.gitlab.repo_scope import apply_gitlab_repo_scope, infer_gitlab_repo_scope
    from integrations.store import STORE_PATH, load_integrations
    from platform.harness_ports import (
        set_github_repo_scope_adapters,
        set_gitlab_repo_scope_adapters,
        set_integration_resolution_adapters,
    )

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

    def _infer(
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, str] | None,
    ) -> tuple[str, str] | None:
        # Port uses positional args; integrations API is keyword-only.
        return infer_github_repo_scope(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached,
        )

    set_github_repo_scope_adapters(infer_scope=_infer, apply_scope=apply_github_repo_scope)

    def _infer_gitlab(
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, str, str] | None,
    ) -> tuple[str, str, str] | None:
        return infer_gitlab_repo_scope(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached,
        )

    set_gitlab_repo_scope_adapters(
        infer_scope=_infer_gitlab,
        apply_scope=apply_gitlab_repo_scope,
    )
    _register_cli_llm_adapters()
    _register_alert_source_detectors()
    _register_incident_anchor_parsers()
    _register_prompt_fragments()


def _register_alert_source_detectors() -> None:
    from core.domain.alerts.alert_source import (
        clear_alert_source_detectors,
        register_alert_source_detector,
    )
    from integrations.grafana.alert_source_detect import detect_grafana_alert_source

    clear_alert_source_detectors()
    register_alert_source_detector(detect_grafana_alert_source)


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
    from integrations.sentry.assistant_prompt import sentry_assistant_prompt_fragment
    from integrations.sentry.gather_prompt import sentry_gather_prompt_fragment
    from integrations.slack.action_prompt import slack_action_prompt_fragment
    from integrations.slack.gather_prompt import slack_gather_prompt_fragment
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

    clear_assistant_prompt_fragments()
    register_assistant_prompt_fragment(sentry_assistant_prompt_fragment)


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
