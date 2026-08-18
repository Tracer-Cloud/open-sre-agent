"""Recovery scan over WAL tool-intent/commit records.

``wal_recorder`` durably appends a ``tool_intent`` before each action tool
executes and a ``tool_call`` commit (same ``tool_call_id``) after it returns.
An intent with no commit in a finished session file means the process died
while that tool was in flight — whether its side effect landed is unknowable
from the log alone, so recovery never replays. Instead the dangling intents
are formatted into a note for the next action turn, and the agent re-discovers
current state (e.g. re-runs the relevant list command) before resuming.
"""

from __future__ import annotations

import json
from typing import Any

# Enough to show a whole interrupted parallel batch without flooding the prompt.
_NOTE_MAX_INTENTS = 5
_ARGS_PREVIEW_MAX_CHARS = 160


def dangling_tool_intents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``tool_intent`` records whose ``tool_call_id`` was never committed."""
    committed = {
        str(rec.get("tool_call_id"))
        for rec in records
        if rec.get("type") == "tool_call" and rec.get("tool_call_id")
    }
    return [
        rec
        for rec in records
        if rec.get("type") == "tool_intent" and str(rec.get("tool_call_id") or "") not in committed
    ]


def _describe_arguments(args: Any) -> str:
    """One-line human/model-readable argument preview.

    Shape-based, not tool-based: anything carrying a ``command`` (plus optional
    ``args`` list — the shell/slash tools) renders as the command line; the
    rest as compact JSON.
    """
    if not isinstance(args, dict) or not args:
        return ""
    command = args.get("command")
    if isinstance(command, str):
        extra = args.get("args")
        if isinstance(extra, list):
            return " ".join([command, *(str(item) for item in extra)])
        return command
    try:
        return json.dumps(args, ensure_ascii=False, default=str)[:_ARGS_PREVIEW_MAX_CHARS]
    except (TypeError, ValueError):
        return str(args)[:_ARGS_PREVIEW_MAX_CHARS]


def _describe_intent(intent: dict[str, Any]) -> str:
    tool = str(intent.get("tool") or "tool")
    detail = _describe_arguments(intent.get("arguments"))
    seq = intent.get("seq")
    step = f" (step {seq})" if isinstance(seq, int) else ""
    body = f"{tool} {detail}".strip()
    return f"{body}{step}"


def format_recovery_note(intents: list[dict[str, Any]]) -> str | None:
    """Format dangling intents into the note injected into the next action turn."""
    if not intents:
        return None
    shown = intents[-_NOTE_MAX_INTENTS:]
    lines = "\n".join(f"- {_describe_intent(intent)}" for intent in shown)
    omitted = len(intents) - len(shown)
    if omitted > 0:
        lines += f"\n- … and {omitted} earlier interrupted call(s)"
    user_text = next(
        (str(intent.get("user_text")) for intent in reversed(intents) if intent.get("user_text")),
        "",
    )
    request_line = f"\nThe interrupted request was: {user_text!r}." if user_text else ""
    return (
        "A previous turn in this session was interrupted while tool calls were "
        "still executing (no result was recorded for them):\n"
        f"{lines}{request_line}\n"
        "Whether their side effects landed is unknown. If the user asks to "
        "continue or the request is still unfinished, re-discover current state "
        "first (re-run the relevant list/status command) and resume only the "
        "steps still missing — never assume an interrupted call completed, and "
        "never blindly repeat steps whose effects already landed."
    )


__all__ = ["dangling_tool_intents", "format_recovery_note"]
