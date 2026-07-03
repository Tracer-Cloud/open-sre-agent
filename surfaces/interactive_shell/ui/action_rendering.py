"""Rendering for the shell tool-calling turn.

This module owns the terminal-facing action observer. Planner tool calls are
internal state by default: the observer records them for history/storage while
the concrete action executors render user-facing command output. The execution
orchestration that drives it lives in
:func:`interactive_shell.runtime.action_turn.run_action_tool_turn`.

Keeping rendering here means the shell turn-entry adapter stays focused on
binding core ports while terminal formatting stays in ``ui/``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from rich.console import Console

from core.agent_harness.agents.action_agent import SELF_RECORDING_ACTION_TOOL_NAMES
from surfaces.interactive_shell.runtime import Session

# Tools whose preview is just ``(label, single-arg)``. The display content is the
# stripped string value of that single argument. Anything that needs to combine
# multiple arguments (``slash_invoke``, ``synthetic_run``) keeps a custom branch
# in :func:`tool_call_display`.
_SIMPLE_TOOL_LABELS: dict[str, tuple[str, str]] = {
    "llm_set_provider": ("LLM provider", "target"),
    "alert_sample": ("sample alert", "template"),
    "investigation_start": ("investigation", "alert_text"),
    "task_cancel": ("cancel task", "target"),
    "cli_exec": ("opensre", "payload"),
    "code_implement": ("implementation", "task"),
    "shell_run": ("shell", "command"),
}


def tool_call_display(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return a ``(label, content)`` pair describing a planned tool call."""
    if tool_name == "slash_invoke":
        command = str(args.get("command", "")).strip()
        raw_args = args.get("args")
        parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
        return "command", " ".join([command, *parsed_args]).strip()
    if tool_name == "synthetic_run":
        suite = str(args.get("suite", "")).strip()
        scenario = str(args.get("scenario", "")).strip()
        return "synthetic test", f"{suite}:{scenario}" if scenario else suite
    simple = _SIMPLE_TOOL_LABELS.get(tool_name)
    if simple is not None:
        label, arg_key = simple
        return label, str(args.get(arg_key, "")).strip()
    return tool_name, json.dumps(args, default=str, sort_keys=True)


class ActionRenderObserver:
    """Agent event observer that records planner turns not owned by action tools.

    Self-recording tools (``slash_invoke``, ``shell_run``, etc.) append their own
    history row; chat turns are recorded later by turn accounting when the
    assistant runs.
    """

    def __init__(self, *, session: Session, console: Console, message: str) -> None:
        self.session = session
        self.console = console
        self.message = message
        self.planned_count = 0

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_update":
            with contextlib.suppress(Exception):
                self.session.storage.append_tool_update(
                    self.session.session_id,
                    tool=str(data.get("name") or "tool"),
                    update=data.get("update"),
                    tool_call_id=str(data.get("id") or "") or None,
                )
            return
        if kind != "tool_start":
            return
        name = str(data.get("name", "")).strip()
        if not name or name == "assistant_handoff":
            return
        if self.planned_count == 0 and name not in SELF_RECORDING_ACTION_TOOL_NAMES:
            self.session.record("cli_agent", self.message)
        self.planned_count += 1


__all__ = [
    "ActionRenderObserver",
    "tool_call_display",
]
