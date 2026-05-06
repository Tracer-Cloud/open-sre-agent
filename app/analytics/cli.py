"""CLI analytics helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.analytics.events import Event
from app.analytics.provider import Properties, get_analytics
from app.utils.sentry_sdk import capture_exception


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping_value(mapping: Mapping[str, object], key: str) -> str | None:
    return _string_value(mapping.get(key))


def _onboard_completed_properties(config: Mapping[str, object]) -> Properties:
    properties: Properties = {}

    wizard_obj = config.get("wizard")
    if isinstance(wizard_obj, Mapping):
        wizard_mode = _mapping_value(wizard_obj, "mode")
        configured_target = _mapping_value(wizard_obj, "configured_target")
        if wizard_mode is not None:
            properties["wizard_mode"] = wizard_mode
        if configured_target is not None:
            properties["configured_target"] = configured_target

    targets_obj = config.get("targets")
    if isinstance(targets_obj, Mapping):
        local_obj = targets_obj.get("local")
        if isinstance(local_obj, Mapping):
            provider = _mapping_value(local_obj, "provider")
            model = _mapping_value(local_obj, "model")
            if provider is not None:
                properties["provider"] = provider
            if model is not None:
                properties["model"] = model

    return properties


def _investigation_started_properties(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> Properties:
    properties: Properties = {
        "has_input_file": input_path is not None,
        "has_inline_json": input_json is not None,
        "interactive": interactive,
    }
    llm_provider = _string_value(os.getenv("LLM_PROVIDER"))
    llm_model = _string_value(os.getenv("ANTHROPIC_MODEL")) or _string_value(
        os.getenv("OPENAI_MODEL")
    )
    if llm_provider is not None:
        properties["llm_provider"] = llm_provider
    if llm_model is not None:
        properties["llm_model"] = llm_model
    return properties


def _capture(event: Event, properties: Properties | None = None) -> None:
    try:
        get_analytics().capture(event, properties)
    except Exception as exc:  # noqa: BLE001
        capture_exception(exc)


def build_cli_invoked_properties(
    *,
    entrypoint: str,
    command_parts: list[str],
    json_output: bool = False,
    verbose: bool = False,
    debug: bool = False,
    yes: bool = False,
    interactive: bool = True,
) -> Properties:
    """Build a structured ``cli_invoked`` payload for any CLI surface.

    Used by ``opensre`` (Click-driven) and the ``python -m app.*`` entrypoints
    so all three end up with the same property names. Records command names
    only — never raw argv values, option values, paths, URLs, or secrets.
    """
    properties: Properties = {
        "entrypoint": entrypoint,
        "command_path": " ".join((entrypoint, *command_parts)),
        "command_family": command_parts[0] if command_parts else "root",
        "json_output": json_output,
        "verbose": verbose,
        "debug": debug,
        "yes": yes,
        "interactive": interactive,
    }
    if len(command_parts) > 1:
        properties["subcommand"] = command_parts[1]
    if command_parts:
        properties["command_leaf"] = command_parts[-1]
    return properties


def capture_cli_invoked(properties: Properties | None = None) -> None:
    _capture(Event.CLI_INVOKED, properties)


def capture_repl_execution_policy_decision(properties: Properties | None = None) -> None:
    _capture(Event.REPL_EXECUTION_POLICY_DECISION, properties)


def capture_onboard_started() -> None:
    _capture(Event.ONBOARD_STARTED)


def capture_onboard_completed(config: Mapping[str, object]) -> None:
    _capture(Event.ONBOARD_COMPLETED, _onboard_completed_properties(config))


def capture_onboard_failed() -> None:
    _capture(Event.ONBOARD_FAILED)


def capture_investigation_started(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> None:
    _capture(
        Event.INVESTIGATION_STARTED,
        _investigation_started_properties(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
        ),
    )


def capture_investigation_completed() -> None:
    _capture(Event.INVESTIGATION_COMPLETED)


def capture_investigation_failed() -> None:
    _capture(Event.INVESTIGATION_FAILED)


def capture_integration_setup_started(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_STARTED, {"service": service})


def capture_integration_setup_completed(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_COMPLETED, {"service": service})


def capture_integrations_listed() -> None:
    _capture(Event.INTEGRATIONS_LISTED)


def capture_integration_removed(service: str) -> None:
    _capture(Event.INTEGRATION_REMOVED, {"service": service})


def capture_integration_verified(service: str) -> None:
    _capture(Event.INTEGRATION_VERIFIED, {"service": service})


def capture_tests_picker_opened() -> None:
    _capture(Event.TESTS_PICKER_OPENED)


def capture_test_synthetic_started(scenario: str, *, mock_grafana: bool) -> None:
    _capture(
        Event.TEST_SYNTHETIC_STARTED,
        {"scenario": scenario, "mock_grafana": mock_grafana},
    )


def capture_tests_listed(category: str, *, search: bool) -> None:
    _capture(Event.TESTS_LISTED, {"category": category, "search": search})


def capture_test_run_started(test_id: str, *, dry_run: bool) -> None:
    _capture(Event.TEST_RUN_STARTED, {"test_id": test_id, "dry_run": dry_run})


def capture_deploy_started(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_STARTED, {"target": target, "dry_run": dry_run})


def capture_deploy_completed(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_COMPLETED, {"target": target, "dry_run": dry_run})


def capture_deploy_failed(*, target: str, dry_run: bool) -> None:
    _capture(Event.DEPLOY_FAILED, {"target": target, "dry_run": dry_run})


def capture_update_started(*, check_only: bool) -> None:
    _capture(Event.UPDATE_STARTED, {"check_only": check_only})


def capture_update_completed(*, check_only: bool, updated: bool) -> None:
    _capture(Event.UPDATE_COMPLETED, {"check_only": check_only, "updated": updated})


def capture_update_failed(*, check_only: bool, reason: str) -> None:
    _capture(Event.UPDATE_FAILED, {"check_only": check_only, "reason": reason})
