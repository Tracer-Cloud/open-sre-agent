from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import EvidenceSource
from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.slack.thread_client import fetch_thread, parse_thread_ref
from integrations.slack.web_client import bot_token_configured


class ReplaySlackThreadLocallyTool(BaseTool):
    name = "replay_slack_thread_locally"
    source: ClassVar[EvidenceSource] = "slack"
    surfaces = ("investigation", "chat", "action")
    side_effect_level = "read_only"
    description = "Fetch a captured Slack thread for local replay and Slack bot behavior testing."
    input_schema = {
        "type": "object",
        "properties": {
            "thread_ref": {"type": "string", "description": "Slack thread in CHANNEL/TS format."}
        },
        "required": ["thread_ref"],
        "additionalProperties": False,
    }
    outputs = {"thread": "Captured Slack thread messages.", "error": "Failure detail."}

    def is_available(self, sources: dict[str, dict[object, object]]) -> bool:
        return bot_token_configured(sources)

    def run(self, thread_ref: str, **_kwargs: Any) -> dict[str, Any]:
        try:
            channel, timestamp = parse_thread_ref(thread_ref)
        except ValueError as exc:
            return {"status": "failed", "error": str(exc), "error_type": "validation_error"}
        thread = fetch_thread(channel, timestamp)
        if "error" in thread:
            error = str(thread["error"])
            error_type = (
                "configuration_or_delivery_error"
                if error.startswith("Slack bot token is not configured.")
                else "delivery_error"
            )
            return {"status": "failed", "error": error, "error_type": error_type}
        return {"status": "ok", "thread": thread}


replay_slack_thread_locally = ReplaySlackThreadLocallyTool()
tool(replay_slack_thread_locally, surfaces=("investigation", "chat", "action"))
