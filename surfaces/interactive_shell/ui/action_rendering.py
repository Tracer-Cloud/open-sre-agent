"""Rendering for the shell tool-calling turn.

This module owns the terminal-facing action observer. It renders invocations
that do not have an executor-owned command banner, records planner turns for
history/storage, and leaves concrete action executors to render their own
command output. The execution orchestration that drives it lives in
:func:`interactive_shell.runtime.action_turn.run_action_tool_turn`.

Keeping rendering here means the shell turn-entry adapter stays focused on
binding core ports while terminal formatting stays in ``ui/``.
"""

from __future__ import annotations

import contextlib
import json
import shlex
from typing import Any

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.accounting import SELF_RECORDING_ACTION_TOOL_NAMES
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal.theme import BOLD_SKILL, DIM, HIGHLIGHT
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.streaming import render_markdown_block
from surfaces.shared.terminal.output.console_state import get_investigation_spinner

# Tools whose preview is just ``(label, single-arg)``. The display content is the
# stripped string value of that single argument. Anything that needs to combine
# multiple arguments (``slash_invoke``, ``synthetic_run``) keeps a custom branch
# in :func:`tool_call_display`.
# The spinner's thinking verb re-rolls once per this many agent-loop steps.
_VERB_ROTATION_STEP_INTERVAL = 2
_TOOL_PREVIEW_MAX_CHARS = 240

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
    """Return a ``(label, content)`` pair describing a planned tool call.

    Both strings are stripped of terminal controls: callers append them to a
    raw Rich line, and the tool name and args are model-supplied.
    """
    if tool_name == "slash_invoke":
        command = str(args.get("command", "")).strip()
        raw_args = args.get("args")
        parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
        label, content = "command", " ".join([command, *parsed_args]).strip()
    elif tool_name == "github_cli":
        raw_args = args.get("args")
        parsed_args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        repo = str(args.get("repo", "")).strip()
        argv = ["gh"]
        if repo and (not parsed_args or parsed_args[0] != "api"):
            argv.extend(["-R", repo])
        argv.extend(parsed_args)
        label, content = "command", shlex.join(argv)
    elif tool_name == "synthetic_run":
        suite = str(args.get("suite", "")).strip()
        scenario = str(args.get("scenario", "")).strip()
        label, content = "synthetic test", f"{suite}:{scenario}" if scenario else suite
    else:
        simple = _SIMPLE_TOOL_LABELS.get(tool_name)
        if simple is not None:
            key_label, arg_key = simple
            label, content = key_label, str(args.get(arg_key, "")).strip()
        else:
            label, content = tool_name, json.dumps(args, default=str, sort_keys=True)
    return strip_terminal_controls(label), strip_terminal_controls(content)


class ActionRenderObserver:
    """Render live agent events and record planner turns.

    Self-recording tools (``slash_invoke``, ``shell_run``, etc.) append their own
    history row and normally render their own invocation. Generic tools are
    rendered here because they have no terminal presenter. Quiet shell calls are
    also rendered here because their executor intentionally withholds its banner
    and output. ``skill_view`` keeps its dedicated activation display.
    """

    def __init__(self, *, session: Session, console: Console, message: str) -> None:
        self.session = session
        self.console = console
        self.message = message
        self.planned_count = 0
        self._pending_skill_calls: dict[str, str] = {}

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "llm_start":
            self._advance_spinner_verb(data)
            return
        if kind == "message_update":
            self._render_intermediate_message(data)
            return
        if kind == "tool_update":
            with contextlib.suppress(Exception):
                self.session.store.append_tool_update(
                    self.session.session_id,
                    tool=str(data.get("name") or "tool"),
                    update=data.get("update"),
                    tool_call_id=str(data.get("id") or "") or None,
                )
            return
        if kind == "tool_end":
            if str(data.get("name", "")).strip() == "skill_view":
                self._render_skill_end(data)
            return
        if kind != "tool_start":
            return
        name = str(data.get("name", "")).strip()
        if not name:
            return
        if name == "skill_view":
            self._render_skill_start(data)
        elif self._observer_owns_invocation(name, data):
            self._render_tool_start(name, data)
        if self.planned_count == 0 and name not in SELF_RECORDING_ACTION_TOOL_NAMES:
            self.session.record("cli_agent", self.message)
        self.planned_count += 1

    @staticmethod
    def _observer_owns_invocation(name: str, data: dict[str, Any]) -> bool:
        if name not in SELF_RECORDING_ACTION_TOOL_NAMES:
            return True
        if name != "shell_run":
            return False
        args = data.get("input")
        quiet = args.get("quiet", False) if isinstance(args, dict) else False
        if isinstance(quiet, str):
            return quiet.strip().lower() in {"1", "true", "yes", "on"}
        return bool(quiet)

    def _render_tool_start(self, name: str, data: dict[str, Any]) -> None:
        args = data.get("input")
        label, content = tool_call_display(name, args if isinstance(args, dict) else {})
        if len(content) > _TOOL_PREVIEW_MAX_CHARS:
            content = f"{content[: _TOOL_PREVIEW_MAX_CHARS - 1]}…"
        line = Text()
        if label == "command" or name == "shell_run":
            line.append("$ ", style=DIM)
            line.append(content)
        else:
            line.append("Calling ", style=DIM)
            line.append(label, style=HIGHLIGHT)
            if content:
                line.append(" ")
                line.append(content)
        self.console.print(line)
        self.console.print()

    def _advance_spinner_verb(self, data: dict[str, Any]) -> None:
        """Rotate the prompt spinner's thinking verb every two agent steps.

        ``llm_start`` fires once per think→act iteration of the agent loop.
        The verb picked at ``SpinnerState.start()`` covers the first two
        iterations; each pair after that re-rolls it, so the label changes
        during a long turn without flickering on every step.
        """
        iteration = int(data.get("iteration", 0) or 0)
        if iteration < _VERB_ROTATION_STEP_INTERVAL or iteration % _VERB_ROTATION_STEP_INTERVAL:
            return
        spinner = get_investigation_spinner()
        if spinner is not None:
            spinner.advance_verb()

    def _render_intermediate_message(self, data: dict[str, Any]) -> None:
        """Render the model's commentary preceding this iteration's tool calls.

        Skills instruct the agent to emit phase headers (``### [n/N] ...``)
        before each tool group; without live rendering that narration is
        dropped. The loop's final no-tool-call answer (``has_tool_calls``
        false) is skipped — the turn driver already streams it as the
        closing reply, so printing it here would duplicate it.
        """
        if not data.get("has_tool_calls"):
            return
        content = str(data.get("content", "")).strip()
        if not content:
            return
        self.console.print()
        # ``render_markdown_block`` sanitizes model text at ``_build_markdown_block``.
        render_markdown_block(self.console, content)

    def _render_skill_start(self, data: dict[str, Any]) -> None:
        """Print ``Skill <name>`` when the agent starts loading a skill."""
        args = data.get("input")
        raw_name = str(args.get("name", "")).strip() if isinstance(args, dict) else ""
        slug = strip_terminal_controls(raw_name.replace("_", "-").lower()) or "skill"
        self._pending_skill_calls[str(data.get("id") or "")] = slug
        # ``Text`` renders the (model-supplied) skill name literally — never
        # through Rich markup.
        line = Text()
        line.append("Skill ", style=BOLD_SKILL)
        line.append(slug, style=HIGHLIGHT)
        self.console.print()
        self.console.print(line)

    def _render_skill_end(self, data: dict[str, Any]) -> None:
        """Print the ``↳`` child line under the skill's ``tool_start`` parent."""
        if self._pending_skill_calls.pop(str(data.get("id") or ""), None) is None:
            return
        output = data.get("output")
        activated = isinstance(output, dict) and bool(output.get("ok"))
        label = "Skill activated" if activated else "Skill failed to load"
        self.console.print(Text(f"  ↳ {label}", style=DIM))
        self.console.print()


__all__ = [
    "ActionRenderObserver",
    "tool_call_display",
]
