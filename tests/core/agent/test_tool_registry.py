"""Tests for interactive-shell action tool registry."""

from __future__ import annotations

import re

from rich.console import Console

from core.agent_harness.tools.action_tools import (
    get_action_tool,
    get_action_tools_from_integrations_context,
)
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
)
from surfaces.cli.wizard.config import PROVIDER_BY_VALUE
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.action_names import TOOL_KIND_TO_NAME


def _action_tools(
    session: Session,
    *,
    resolved_integrations: dict[str, dict[str, str]] | None = None,
) -> list[object]:
    ctx = ActionToolContext(session=session, console=Console(force_terminal=False))
    return get_action_tools_from_integrations_context(
        ctx, resolved_integrations=resolved_integrations
    )


def _tool_specs(
    session: Session,
    *,
    resolved_integrations: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.public_input_schema,
        }
        for tool in _action_tools(session, resolved_integrations=resolved_integrations)
    ]


# OpenAI's Chat Completions API rejects any tool name that does not match
# this pattern with HTTP 400. Every OpenAI-compatible provider (OpenRouter,
# Gemini, Nvidia, Minimax, Ollama, etc.) enforces the same rule. Anthropic
# is more permissive, but using the OpenAI subset keeps the planner working
# across all providers without per-provider name munging.
_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def test_action_kind_mapping_targets_registered_tools() -> None:
    for tool_name in TOOL_KIND_TO_NAME.values():
        assert get_action_tool(tool_name) is not None


def test_tool_specs_include_required_fields() -> None:
    specs = _tool_specs(Session())
    assert specs
    for spec in specs:
        assert spec["name"]
        assert spec["description"]
        assert "input_schema" in spec


def test_action_kind_to_tool_names_are_openai_compatible() -> None:
    """Guard against the dotted-name regression that broke all 56 live
    planner scenarios on OpenAI-style providers (HTTP 400 on
    ``tools[0].function.name``)."""
    for kind, tool_name in TOOL_KIND_TO_NAME.items():
        assert _OPENAI_TOOL_NAME_RE.match(tool_name), (
            f"TOOL_KIND_TO_NAME[{kind!r}] = {tool_name!r} must match "
            f"OpenAI's tool-name pattern ^[a-zA-Z0-9_-]+$"
        )


def test_registered_tool_specs_are_openai_compatible() -> None:
    """Same guarantee, but exercised through the spec builder the LLM
    planner actually feeds to the provider."""
    specs = _tool_specs(Session())
    assert specs
    for spec in specs:
        name = spec["name"]
        assert _OPENAI_TOOL_NAME_RE.match(name), (
            f"Registered tool spec name {name!r} must match "
            f"OpenAI's tool-name pattern ^[a-zA-Z0-9_-]+$"
        )


def test_tool_schemas_are_closed_objects() -> None:
    specs = _tool_specs(Session())
    assert specs
    for spec in specs:
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_required_properties_have_descriptions() -> None:
    specs = _tool_specs(Session())
    assert specs
    for spec in specs:
        schema = spec["input_schema"]
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for required_name in required:
            prop = properties.get(required_name)
            assert isinstance(prop, dict), (
                f"{spec['name']} required property {required_name!r} missing from properties"
            )
            assert str(prop.get("description", "")).strip(), (
                f"{spec['name']} required property {required_name!r} must include description"
            )


def test_llm_set_provider_schema_enum_matches_runtime_providers() -> None:
    spec = next(tool for tool in _tool_specs(Session()) if tool["name"] == "llm_set_provider")
    target = spec["input_schema"]["properties"]["target"]
    target_variants = target.get("oneOf", [])
    enum_variant = next(
        variant for variant in target_variants if isinstance(variant, dict) and "enum" in variant
    )
    assert set(enum_variant["enum"]) == set(PROVIDER_BY_VALUE.keys())


def test_slash_invoke_schema_enum_matches_registered_commands() -> None:
    spec = next(tool for tool in _tool_specs(Session()) if tool["name"] == "slash_invoke")
    command = spec["input_schema"]["properties"]["command"]
    assert set(command["enum"]) == set(SLASH_COMMANDS.keys())


def test_tools_hidden_when_capabilities_are_explicitly_empty() -> None:
    session = Session(
        available_capabilities={
            "slash_commands": (),
            "cli_commands": (),
            "synthetic_suites": (),
            "shell_commands": (),
            "implementation": (),
            "llm_provider": (),
            "investigation": (),
            "task_cancel": (),
        }
    )
    names = {spec["name"] for spec in _tool_specs(session)}
    assert "slash_invoke" not in names
    assert "cli_exec" not in names
    assert "synthetic_run" not in names
    assert "shell_run" not in names
    assert "code_implement" not in names
    assert "llm_set_provider" not in names
    assert "investigation_start" not in names
    assert "alert_sample" not in names
    assert "task_cancel" not in names


def test_telegram_send_message_offered_when_telegram_is_configured() -> None:
    session = Session()
    session.configured_integrations = ("telegram",)
    session.configured_integrations_known = True
    names = {
        spec["name"]
        for spec in _tool_specs(
            session,
            resolved_integrations={"telegram": {"bot_token": "token"}},
        )
    }
    assert "telegram_send_message" in names


def test_telegram_send_message_hidden_when_telegram_is_not_configured() -> None:
    names = {spec["name"] for spec in _tool_specs(Session())}
    assert "telegram_send_message" not in names


def test_rocketchat_send_message_offered_when_rocketchat_is_configured() -> None:
    session = Session()
    session.configured_integrations = ("rocketchat",)
    session.configured_integrations_known = True
    names = {
        spec["name"]
        for spec in _tool_specs(
            session,
            resolved_integrations={
                "rocketchat": {
                    "server_url": "https://chat.example.com",
                    "auth_token": "token",
                    "user_id": "u1",
                }
            },
        )
    }
    assert "rocketchat_send_message" in names


def test_rocketchat_send_message_hidden_when_rocketchat_is_not_configured() -> None:
    names = {spec["name"] for spec in _tool_specs(Session())}
    assert "rocketchat_send_message" not in names


def test_llm_set_provider_offered_by_default() -> None:
    """With no capability constraints (the production default), the planner is
    still offered the provider-switch tool."""
    names = {spec["name"] for spec in _tool_specs(Session())}
    assert "llm_set_provider" in names


def test_registry_agent_tools_exclude_unavailable_tool() -> None:
    session = Session(available_capabilities={"slash_commands": ()})
    ctx = ActionToolContext(session=session, console=Console(force_terminal=False))
    names = {tool.name for tool in get_action_tools_from_integrations_context(ctx)}
    assert "slash_invoke" not in names


def test_investigation_offered_to_planner() -> None:
    """``investigation_start`` is always offered to the planner so diagnostic
    prompts can trigger the RCA pipeline from the REPL."""
    names = {spec["name"] for spec in _tool_specs(Session())}
    assert "investigation_start" in names


def test_investigation_tool_description_preserves_compound_slash_guidance() -> None:
    entry = get_action_tool("investigation_start")
    assert entry is not None
    description = entry.description.lower()
    assert "run /remote and then investigate" in description
    assert "placeholder quoted text like 'hello world'" in description
    assert "separate second tool call" in description
    assert "never drop the quoted investigation" in description


def test_assistant_handoff_description_preserves_bare_alert_guidance() -> None:
    entry = get_action_tool("assistant_handoff")
    assert entry is not None
    description = entry.description.lower()
    assert "bare pasted alert json/yaml/key-value blob" in description
    assert "did not explicitly ask to investigate" in description


def test_slash_tool_description_preserves_compound_followup_guidance() -> None:
    entry = get_action_tool("slash_invoke")
    assert entry is not None
    description = entry.description.lower()
    assert "only the slash-command clause" in description
    assert "run /remote and then investigate" in description
    assert "investigation_start" in description


def test_synthetic_tool_description_preserves_numeric_id_guidance() -> None:
    entry = get_action_tool("synthetic_run")
    assert entry is not None
    description = entry.description.lower()
    assert '"005" -> "005-failover"' in description
    assert '"004" -> "004-cpu-saturation-bad-query"' in description
    assert "never substitute a neighboring numbered scenario" in description


def test_gateway_capabilities_only_hide_gateway_unsupported_tools() -> None:
    session = Session(
        available_capabilities={
            "investigation": (),
            "llm_provider": (),
            "task_cancel": (),
        }
    )

    names = {spec["name"] for spec in _tool_specs(session)}

    assert "investigation_start" not in names
    assert "alert_sample" not in names
    assert "llm_set_provider" not in names
    assert "task_cancel" not in names
    assert "slash_invoke" in names
