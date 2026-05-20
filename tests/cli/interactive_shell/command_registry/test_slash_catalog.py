"""Tests for slash-command MCP catalog."""

from __future__ import annotations

from app.cli.interactive_shell.command_registry import SLASH_COMMANDS
from app.cli.interactive_shell.command_registry.slash_catalog import (
    _MCP_BY_COMMAND,
    build_slash_command_specs,
    format_slash_catalog_text,
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    REGISTRY,
)

_MIN_LLM_DESCRIPTION_LEN = 20


def test_slash_catalog_covers_all_registered_commands() -> None:
    assert len(_MCP_BY_COMMAND) == len(SLASH_COMMANDS)
    assert set(_MCP_BY_COMMAND) == set(SLASH_COMMANDS.keys())
    specs = build_slash_command_specs()
    assert len(specs) == len(SLASH_COMMANDS)


def test_slash_command_specs_have_mcp_metadata() -> None:
    for spec in build_slash_command_specs():
        assert len(spec.llm_description) >= _MIN_LLM_DESCRIPTION_LEN, spec.name
        assert spec.use_cases, spec.name


def test_slash_invoke_tool_description_lists_every_command() -> None:
    description = slash_invoke_tool_description()
    for name in SLASH_COMMANDS:
        assert name in description


def test_slash_invoke_schema_enum_matches_slash_commands() -> None:
    schema = slash_invoke_input_schema()
    command = schema["properties"]["command"]
    assert set(command["enum"]) == set(SLASH_COMMANDS.keys())


def test_registered_slash_invoke_uses_catalog() -> None:
    entry = REGISTRY.get("slash_invoke")
    assert entry is not None
    assert len(entry.description) > 200
    assert set(entry.input_schema["properties"]["command"]["enum"]) == set(SLASH_COMMANDS.keys())


def test_format_slash_catalog_text_compact_is_non_empty() -> None:
    text = format_slash_catalog_text(compact=True)
    assert text
    assert "**/health**" in text
