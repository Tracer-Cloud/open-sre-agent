"""Property builders for analytics events."""

from __future__ import annotations

import os
from collections.abc import Mapping

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from config.constants.llm import LLM_PROVIDER_ENV
from config.llm_auth.provider_catalog import provider_spec
from infrastructure.analytics.investigation_tracker_types import (
    InvestigationTracker,
    _with_investigation_loop_metrics,
)
from infrastructure.analytics.provider import Properties
from infrastructure.analytics.repl_context import get_cli_session_id


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _configured_llm_model() -> str | None:
    """Return the reasoning/legacy model env for the active LLM provider."""
    provider = _string_value(os.getenv(LLM_PROVIDER_ENV))
    candidates: list[str] = []
    if provider is not None:
        spec = provider_spec(provider)
        if spec is not None:
            candidates.extend(key for key in (spec.model_env, spec.legacy_model_env) if key)
    if not candidates:
        for fallback_provider in ("anthropic", "openai"):
            spec = provider_spec(fallback_provider)
            if spec is None:
                continue
            candidates.extend(key for key in (spec.model_env, spec.legacy_model_env) if key)
    for key in candidates:
        value = _string_value(os.getenv(key))
        if value is not None:
            return value
    return None


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
    evaluate_requested: bool,
    shared_properties: Properties,
) -> Properties:
    properties: Properties = {
        **shared_properties,
        "has_input_file": input_path is not None,
        "has_inline_json": input_json is not None,
        "interactive": interactive,
        "evaluate_requested": evaluate_requested,
    }
    llm_provider = _string_value(os.getenv(LLM_PROVIDER_ENV))
    llm_model = _configured_llm_model()
    if llm_provider is not None:
        properties["llm_provider"] = llm_provider
    if llm_model is not None:
        properties["llm_model"] = llm_model
    return _with_investigation_loop_metrics(
        properties,
        loop_count=0,
        iteration_cap=MAX_INVESTIGATION_LOOPS,
    )


def _investigation_completed_properties(
    *,
    shared_properties: Properties,
    tracker: InvestigationTracker | None = None,
    state: Mapping[str, object] | None = None,
) -> Properties:
    return _with_investigation_loop_metrics(
        {**shared_properties},
        state=state,
        tracker=tracker,
    )


def _investigation_failed_properties(
    *,
    shared_properties: Properties,
    failure_type: str | None = None,
    failure_message: str | None = None,
    failure_detail: str | None = None,
    failure_category: str | None = None,
    integration_involved: str | None = None,
    integration_failure_message: str | None = None,
    investigation_target: str | None = None,
    state: Mapping[str, object] | None = None,
    tracker: InvestigationTracker | None = None,
) -> Properties:
    properties: Properties = {**shared_properties}
    if failure_type:
        properties["failure_type"] = failure_type
    if failure_message:
        properties["failure_message"] = failure_message
    if failure_detail:
        properties["failure_detail"] = failure_detail
    if failure_category:
        properties["failure_category"] = failure_category
    if integration_involved:
        properties["integration_involved"] = integration_involved
    if integration_failure_message:
        properties["integration_failure_message"] = integration_failure_message
    if investigation_target:
        properties["investigation_target"] = investigation_target
    return _with_investigation_loop_metrics(properties, state=state, tracker=tracker)


def _investigation_outcome_properties(
    *,
    investigation_id: str,
    status: str,
    investigation_target: str,
    root_cause_excerpt: str = "",
    error_excerpt: str = "",
    failure_category: str | None = None,
    integration_involved: str | None = None,
    integration_failure_message: str | None = None,
    failure_detail: str | None = None,
    state: Mapping[str, object] | None = None,
) -> Properties:
    properties: Properties = {
        "investigation_id": investigation_id,
        "status": status,
        "investigation_target": investigation_target,
    }
    if root_cause_excerpt:
        properties["root_cause_excerpt"] = root_cause_excerpt
    if error_excerpt:
        properties["error_excerpt"] = error_excerpt
    if failure_category:
        properties["failure_category"] = failure_category
    if integration_involved:
        properties["integration_involved"] = integration_involved
    if integration_failure_message:
        properties["integration_failure_message"] = integration_failure_message
    if failure_detail:
        properties["failure_detail"] = failure_detail
    session_id = get_cli_session_id()
    if session_id:
        properties["cli_session_id"] = session_id
    return _with_investigation_loop_metrics(properties, state=state)


def _integration_lifecycle_properties(service: str) -> Properties:
    properties: Properties = {"service": service}
    session_id = get_cli_session_id()
    if session_id:
        properties["cli_session_id"] = session_id
    return properties


def _bucket_duration_ms(duration_ms: float) -> str:
    if duration_ms < 500:
        return "<500ms"
    if duration_ms < 1000:
        return "500ms-1s"
    if duration_ms < 3000:
        return "1s-3s"
    if duration_ms < 5000:
        return "3s-5s"
    return ">=5s"


def _bucket_percentage(percent: float) -> str:
    if percent < 25:
        return "0-24"
    if percent < 50:
        return "25-49"
    if percent < 75:
        return "50-74"
    if percent < 95:
        return "75-94"
    return "95-100"


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
