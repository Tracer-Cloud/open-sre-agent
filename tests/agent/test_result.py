from __future__ import annotations

from app.agent.result import _extract_last_assistant_text


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

    assert _extract_last_assistant_text(messages) == (
        "## Diagnosis\n Root cause: missing telemetry"
    )
