"""Tests for interactive-shell action tool registry."""

from __future__ import annotations

import re

from rich.console import Console

from cli.wizard.config import PROVIDER_BY_VALUE
from core.agent_harness.session import ReplSession
from interactive_shell.command_registry import SLASH_COMMANDS
from tools.interactive_shell.contracts import (
    ToolContext,
)
from tools.interactive_shell.registry import (
    REGISTRY,
    TOOL_KIND_TO_NAME,
)

# OpenAI's Chat Completions API rejects any tool name that does not match
# this pattern with HTTP 400. Every OpenAI-compatible provider (OpenRouter,
# Gemini, Nvidia, Minimax, Ollama, etc.) enforces the same rule. Anthropic
# is more permissive, but using the OpenAI subset keeps the planner working
# across all providers without per-provider name munging.
_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def test_action_kind_mapping_targets_registered_tools() -> None:
    for tool_name in TOOL_KIND_TO_NAME.values():
        assert REGISTRY.get(tool_name) is not None


def test_tool_specs_include_required_fields() -> None:
    specs = REGISTRY.tool_specs_for_llm(ReplSession())
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
    specs = REGISTRY.tool_specs_for_llm(ReplSession())
    assert specs
    for spec in specs:
        name = spec["name"]
        assert _OPENAI_TOOL_NAME_RE.match(name), (
            f"Registered tool spec name {name!r} must match "
            f"OpenAI's tool-name pattern ^[a-zA-Z0-9_-]+$"
        )


def test_tool_schemas_are_closed_objects() -> None:
    specs = REGISTRY.tool_specs_for_llm(ReplSession())
    assert specs
    for spec in specs:
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_required_properties_have_descriptions() -> None:
    specs = REGISTRY.tool_specs_for_llm(ReplSession())
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
    spec = next(
        tool
        for tool in REGISTRY.tool_specs_for_llm(ReplSession())
        if tool["name"] == "llm_set_provider"
    )
    target = spec["input_schema"]["properties"]["target"]
    target_variants = target.get("oneOf", [])
    enum_variant = next(
        variant for variant in target_variants if isinstance(variant, dict) and "enum" in variant
    )
    assert set(enum_variant["enum"]) == set(PROVIDER_BY_VALUE.keys())


def test_slash_invoke_schema_enum_matches_registered_commands() -> None:
    spec = next(
        tool
        for tool in REGISTRY.tool_specs_for_llm(ReplSession())
        if tool["name"] == "slash_invoke"
    )
    command = spec["input_schema"]["properties"]["command"]
    assert set(command["enum"]) == set(SLASH_COMMANDS.keys())


def test_tools_hidden_when_capabilities_are_explicitly_empty() -> None:
    session = ReplSession(
        available_capabilities={
            "slash_commands": (),
            "cli_commands": (),
            "synthetic_suites": (),
            "shell_commands": (),
            "implementation": (),
            "llm_provider": (),
        }
    )
    names = {spec["name"] for spec in REGISTRY.tool_specs_for_llm(session)}
    assert "slash_invoke" not in names
    assert "cli_exec" not in names
    assert "synthetic_run" not in names
    assert "shell_run" not in names
    assert "code_implement" not in names
    assert "llm_set_provider" not in names


def test_llm_set_provider_offered_by_default() -> None:
    """With no capability constraints (the production default), the planner is
    still offered the provider-switch tool."""
    names = {spec["name"] for spec in REGISTRY.tool_specs_for_llm(ReplSession())}
    assert "llm_set_provider" in names


def test_registry_agent_tools_exclude_unavailable_tool() -> None:
    session = ReplSession(available_capabilities={"slash_commands": ()})
    ctx = ToolContext(session=session, console=Console(force_terminal=False))
    names = {tool.name for tool in REGISTRY.agent_tools_for_context(ctx)}
    assert "slash_invoke" not in names


def test_investigation_offered_to_planner() -> None:
    """``investigation_start`` is always offered to the planner so diagnostic
    prompts can trigger the RCA pipeline from the REPL."""
    names = {spec["name"] for spec in REGISTRY.tool_specs_for_llm(ReplSession())}
    assert "investigation_start" in names


def test_investigation_tool_description_preserves_compound_slash_guidance() -> None:
    entry = REGISTRY.get("investigation_start")
    assert entry is not None
    description = entry.description.lower()
    assert "run /remote and then investigate" in description
    assert "placeholder quoted text like 'hello world'" in description
    assert "separate second tool call" in description
    assert "never drop the quoted investigation" in description


def test_assistant_handoff_description_preserves_bare_alert_guidance() -> None:
    entry = REGISTRY.get("assistant_handoff")
    assert entry is not None
    description = entry.description.lower()
    assert "bare pasted alert json/yaml/key-value blob" in description
    assert "did not explicitly ask to investigate" in description


def test_slash_tool_description_preserves_compound_followup_guidance() -> None:
    entry = REGISTRY.get("slash_invoke")
    assert entry is not None
    description = entry.description.lower()
    assert "only the slash-command clause" in description
    assert "run /remote and then investigate" in description
    assert "investigation_start" in description


def test_synthetic_tool_description_preserves_numeric_id_guidance() -> None:
    entry = REGISTRY.get("synthetic_run")
    assert entry is not None
    description = entry.description.lower()
    assert '"005" -> "005-failover"' in description
    assert '"004" -> "004-cpu-saturation-bad-query"' in description
    assert "never substitute a neighboring numbered scenario" in description
