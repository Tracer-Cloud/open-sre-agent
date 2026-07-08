from __future__ import annotations

from core.domain.diagnosis import (
    InvestigationResult,
    build_diagnosis_schema,
    result_to_state,
    taxonomy_categories_for_alert_source,
)
from core.messages.transcript import extract_last_assistant_text


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


def test_result_to_state_strips_internal_markers_from_agent_messages() -> None:
    """Diagnose is the last stage to touch agent_messages — its eviction markers
    must not leak into the persisted investigation state."""
    result = InvestigationResult(
        root_cause="disk full",
        root_cause_category="disk_pressure",
        agent_messages=[
            {"role": "user", "content": "alert", "_opensre_seed": True},
            {"role": "assistant", "content": "ok", "_opensre_duplicate_result": True},
        ],
    )

    state = result_to_state(result)

    assert state["agent_messages"] == [
        {"role": "user", "content": "alert"},
        {"role": "assistant", "content": "ok"},
    ]
    assert result.agent_messages[0]["_opensre_seed"] is True
    assert result.agent_messages[1]["_opensre_duplicate_result"] is True
