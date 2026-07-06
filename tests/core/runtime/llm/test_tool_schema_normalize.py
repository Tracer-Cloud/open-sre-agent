"""Tests for shared strict tool schema normalization."""

from __future__ import annotations

from core.llm.shared.tool_schema_normalize import (
    BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    COMMON_UNSUPPORTED_SCHEMA_KEYS,
    normalize_openai_tool_input_schema,
    sanitize_strict_tool_schema,
)


def test_openai_normalizer_preserves_additional_properties_false() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }
    cleaned = normalize_openai_tool_input_schema(schema)
    assert cleaned["additionalProperties"] is False


def test_bedrock_normalizer_strips_additional_properties_false() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }
    cleaned = sanitize_strict_tool_schema(schema, unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS)
    assert "additionalProperties" not in cleaned


def test_openai_and_bedrock_share_composite_flattening() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    openai_cleaned = sanitize_strict_tool_schema(
        schema, unsupported_keys=COMMON_UNSUPPORTED_SCHEMA_KEYS
    )
    bedrock_cleaned = sanitize_strict_tool_schema(
        schema, unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS
    )
    assert openai_cleaned == bedrock_cleaned == {"type": "string"}
