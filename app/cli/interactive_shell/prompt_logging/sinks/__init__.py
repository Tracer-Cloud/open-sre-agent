"""Prompt logging sinks."""

from app.cli.interactive_shell.prompt_logging.sinks.local_jsonl import append_prompt_log_record
from app.cli.interactive_shell.prompt_logging.sinks.posthog_ai import capture_ai_generation

__all__ = ["append_prompt_log_record", "capture_ai_generation"]
