"""Property builders for analytics events."""

from __future__ import annotations

from collections.abc import Mapping

from infrastructure.analytics.provider import Properties
from infrastructure.analytics.repl_context import get_cli_session_id


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
