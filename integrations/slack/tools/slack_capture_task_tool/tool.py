"""Capture lightweight tasks/reminders from Slack (or any surface)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config.constants as const_module
from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE

_STORE_NAME = "slack_captured_tasks.jsonl"


def _store_path() -> Path:
    return const_module.OPENSRE_HOME_DIR / _STORE_NAME


def append_captured_task(*, text: str, requester: str = "", channel_id: str = "") -> dict[str, str]:
    """Append one captured task line; return the stored record."""
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "text": text.strip(),
        "requester": requester.strip(),
        "channel_id": channel_id.strip(),
        "status": "open",
    }
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


class SlackCaptureTaskTool(BaseTool):
    """Persist a short task/reminder captured from Slack conversation."""

    name = "slack_capture_task"
    source = SOURCE
    description = (
        "Capture a task or reminder from the conversation into the local OpenSRE "
        "task list (~/.opensre/slack_captured_tasks.jsonl). Use when the user says "
        "'add task …', 'remind me …', or 'todo: …'. Confirm back with the saved text."
    )
    use_cases = [
        "Adding a follow-up from a Slack thread",
        "Capturing a reminder the user asked OpenSRE to remember",
    ]
    anti_examples = [
        "Scheduling a cron digest (use opensre cron / sentry digest schedule)",
        "Creating a GitHub issue (use GitHub tools)",
    ]
    requires = ["slack"]
    side_effect_level = "external"
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Task or reminder text to store.",
            },
            "requester": {
                "type": "string",
                "description": "Optional Slack user id who asked.",
            },
            "channel_id": {
                "type": "string",
                "description": "Optional Slack channel id where it was asked.",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'captured' on success, 'failed' otherwise",
        "task": "stored task record",
        "error": "error detail when failed",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        del sources
        return True

    def run(
        self,
        text: str,
        requester: str = "",
        channel_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": "text cannot be empty.",
                "error_type": "validation_error",
            }
        record = append_captured_task(text=cleaned, requester=requester, channel_id=channel_id)
        return {
            "source": SOURCE,
            "available": True,
            "status": "captured",
            "task": record,
        }


slack_capture_task = tool(
    SlackCaptureTaskTool(),
    surfaces=("investigation", "chat", "action"),
)
