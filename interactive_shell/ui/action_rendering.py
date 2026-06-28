"""Rendering for the shell tool-calling turn.

This module owns the *user-facing* half of tool-calling execution: it formats
tool calls into human-readable labels and prints the "Requested actions" preview
as the action agent streams its tool calls. The execution orchestration that
drives it lives in
:func:`interactive_shell.runtime.shell_turn_execution.run_action_tool_turn`.

Keeping rendering here means the shell turn-entry adapter stays focused on
binding core ports while terminal formatting stays in ``ui/``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from rich.console import Console
from rich.markup import escape

from interactive_shell.runtime import ReplSession
from interactive_shell.ui import BOLD_BRAND, DIM
from interactive_shell.ui.streaming import render_response_header

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
    """Agent event observer that prints the "Requested actions" preview."""

    def __init__(self, *, session: ReplSession, console: Console, message: str) -> None:
        self.session = session
        self.console = console
        self.message = message
        self.planned_count = 0
        self._recorded_cli_agent = False

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
        tool_input = data.get("input")
        args = tool_input if isinstance(tool_input, dict) else {}
        if self.planned_count == 0:
            self.console.print()
            render_response_header(self.console, "assistant")
            self.console.print(f"[{DIM}]Requested actions:[/]")
            self.session.record("cli_agent", self.message)
            self._recorded_cli_agent = True
        self.planned_count += 1
        label, content = tool_call_display(name, args)
        self.console.print(
            f"[{DIM}]{self.planned_count}.[/] [{BOLD_BRAND}]{label}[/] {escape(content)}"
        )


__all__ = [
    "ActionRenderObserver",
    "tool_call_display",
]
