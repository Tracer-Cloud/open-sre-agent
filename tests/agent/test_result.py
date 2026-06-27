from __future__ import annotations

from core.domain.diagnosis import (
    build_diagnosis_schema,
    extract_last_assistant_text,
    taxonomy_categories_for_alert_source,
)


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    type = "tool_use"
    text = "should be ignored"


def test_extract_last_assistant_text_handles_anthropic_content_blocks() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [
                _TextBlock("## Diagnosis\n"),
                _ToolUseBlock(),
                {"type": "text", "text": "Root cause: missing telemetry"},
            ],
        },
    ]

    assert extract_last_assistant_text(messages) == ("## Diagnosis\n Root cause: missing telemetry")


def test_non_hermes_taxonomy_excludes_hermes_categories() -> None:
    categories = taxonomy_categories_for_alert_source("postgresql")
    assert "agent_hang" not in categories
    assert "ghost_session" not in categories


def test_hermes_taxonomy_is_scoped_to_hermes_categories() -> None:
    categories = taxonomy_categories_for_alert_source("hermes")
    assert "agent_hang" in categories
    assert "ghost_session" in categories
    assert "connection_exhaustion" not in categories

    schema = build_diagnosis_schema(categories)
    description = str(schema.model_fields["root_cause_category"].description)
    assert "agent_hang" in description
    assert "connection_exhaustion" not in description
