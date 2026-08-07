"""Queue an interactive selection menu for a decision the user must make.

Raw-stdin pickers cannot run mid-turn: the REPL keeps a ``prompt_async()`` open
concurrently, so arrow-key reads would race it and terminal CPR replies would
leak into the input line (same constraint as the deferred pickers in
``actions/slash.py``). This tool therefore stores the question as a
:class:`~core.agent_harness.session.pending_choice.PendingUserChoice` and queues
the literal ``/choose`` command, which the loop dispatches with exclusive stdin.
The selected option label is auto-submitted as the next user message.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.terminal_access import session_terminal, set_auto_command
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    execute_with_action_context,
    object_schema,
    string_array_property,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool

_MIN_OPTIONS = 2
_MAX_OPTIONS = 8
_CHOOSE_COMMAND = "/choose"

_FALLBACK_INSTRUCTION = (
    "No interactive selection menu is available on this surface. Present the "
    "options as a short numbered list instead and ask the user to reply with "
    "the number or the option text."
)
_QUEUED_INSTRUCTION = (
    "The selection menu opens after this turn ends. End the turn now with at "
    "most one short sentence of context; do NOT repeat the options as text and "
    "do NOT ask the user to type a number. The user's selection arrives as the "
    "next user message (the chosen option label, verbatim)."
)


def _menu_available(ctx: ActionToolContext) -> bool:
    """True when the REPL can render the deferred ``/choose`` picker.

    Mirrors ``_slash_drives_interactive_picker``: gateway/headless sessions have
    no terminal facet, and non-TTY turns must not queue a picker back to a REPL
    loop that does not exist (e.g. gateway running under tmux with a TTY stdin).
    """
    if ctx.is_tty is False or session_terminal(ctx.session) is None:
        return False
    ports = ctx.slash_ports
    return ports is not None and bool(ports.tty_interactive())


def execute_ask_user_choice_tool(args: dict[str, Any], ctx: ActionToolContext) -> dict[str, Any]:
    title = str(args.get("title", "")).strip()
    raw_options = args.get("options")
    options: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_options, list):
        for item in raw_options:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                options.append(text)

    if not title:
        return {"ok": False, "error": "title is required"}
    if len(options) < _MIN_OPTIONS:
        return {
            "ok": False,
            "error": f"at least {_MIN_OPTIONS} distinct non-empty options are required",
        }
    if len(options) > _MAX_OPTIONS:
        return {"ok": False, "error": f"at most {_MAX_OPTIONS} options are supported"}

    if not _menu_available(ctx):
        return {"ok": True, "menu": "unavailable", "instruction": _FALLBACK_INSTRUCTION}

    ctx.session.pending_user_choice = PendingUserChoice(title=title, options=tuple(options))
    set_auto_command(ctx.session, _CHOOSE_COMMAND)
    return {
        "ok": True,
        "menu": "queued",
        "summary": f"selection menu queued: {title}",
        "instruction": _QUEUED_INSTRUCTION,
    }


def run_ask_user_choice(
    *,
    title: str,
    options: list[str] | None = None,
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {"title": title, "options": options or []},
        context,
        execute_ask_user_choice_tool,
    )


ask_user_choice_tool = RegisteredTool(
    name="ask_user_choice",
    description=(
        "Ask the user to pick ONE option from a small fixed set via the "
        "interactive shell's arrow-key selection menu. Use this INSTEAD of "
        "writing a numbered list ('Reply with 1, 2, or 3') whenever a required "
        "decision blocks further progress. The menu opens after the turn ends "
        "and the chosen option label arrives verbatim as the next user "
        "message, so end the turn right after calling this. If the result "
        "says the menu is unavailable, fall back to a numbered list."
    ),
    use_cases=[
        (
            "A workflow is blocked on one required decision between a small "
            "fixed set of actions (e.g. stash vs commit vs worktree)"
        ),
        "A skill instructs presenting a structured choice / dropdown to the user",
    ],
    anti_examples=[
        "Open-ended questions with no fixed option set (ask in plain text)",
        "Yes/no confirmations already covered by the execution confirmation flow",
        "Presenting information that requires no decision",
    ],
    input_schema=object_schema(
        properties={
            "title": string_property(
                description=(
                    "Short question shown as the menu title, e.g. 'How should "
                    "I handle the uncommitted changes?'"
                ),
                min_length=1,
            ),
            "options": string_array_property(
                description=(
                    "Two to eight short option labels, recommended option "
                    "first, e.g. ['Stash the changes (recommended)', 'Commit "
                    "the changes', 'Use a separate git worktree']. The "
                    "selected label is echoed back verbatim as the user's "
                    "reply, so each label must be self-explanatory."
                ),
            ),
        },
        required=("title", "options"),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_ask_user_choice,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level="read_only",
)


__all__ = [
    "ask_user_choice_tool",
    "execute_ask_user_choice_tool",
    "run_ask_user_choice",
]
