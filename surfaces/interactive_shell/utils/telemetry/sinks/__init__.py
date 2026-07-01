"""Prompt logging sinks."""

from surfaces.interactive_shell.utils.telemetry.sinks.local_jsonl import (
    append_prompt_log_record,
)
from surfaces.interactive_shell.utils.telemetry.sinks.posthog_ai import capture_ai_generation

__all__ = ["append_prompt_log_record", "capture_ai_generation"]
