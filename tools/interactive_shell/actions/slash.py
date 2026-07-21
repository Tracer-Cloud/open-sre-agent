"""Slash command tool."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from core.agent_harness.session.terminal_access import (
    agent_turn_executed_slashes,
    exclusive_stdin_active,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.tool_framework.registered_tool import RegisteredTool
from tools.interactive_shell.shared import plan_foreground_tool
from tools.interactive_shell.shared.slash_catalog import (
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)

# Slash commands that drive a raw-stdin inline picker or wizard (questionary /
# repl_choose_one). When the action agent resolves free text (e.g. "remove
# github") into one of these, the REPL loop has NOT reserved exclusive stdin for
# the turn — it only does so for deterministically-typed commands. Running the
# picker inline then races the concurrently open prompt_async() for stdin and the
# terminal's cursor-position replies (ESC[row;colR) leak into the input line as
# literal keystrokes. Defer them through ``set_auto_command`` so the loop
# re-dispatches the command as a deterministic turn it runs with exclusive stdin.
_INTERACTIVE_PICKER_MENUS: frozenset[str] = frozenset({"/auth", "/login", "/integrations", "/mcp"})
_INTERACTIVE_PICKER_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("/auth", "login"),
        ("/auth", "logout"),
        ("/integrations", "setup"),
        ("/integrations", "remove"),
        ("/mcp", "connect"),
        ("/mcp", "disconnect"),
    }
)


def _slash_drives_interactive_picker(
    name: str,
    slash_args: list[str],
    *,
    session: Any,
    is_tty: bool | None,
    ports: Any,
) -> bool:
    """True when a planned slash command opens a raw-stdin inline picker/wizard.

    Only relevant in an interactive REPL with a terminal facet: gateway/headless
    sessions always run inline, and non-TTY turns must not queue back to a REPL
    loop that does not exist (e.g. gateway running under tmux with a TTY stdin).
    """
    if is_tty is False or session_terminal(session) is None:
        return False
    if not ports.tty_interactive():
        return False
    if name == "/login":
        return True
    if not slash_args:
        return name in _INTERACTIVE_PICKER_MENUS
    return (name, slash_args[0].lower()) in _INTERACTIVE_PICKER_SUBCOMMANDS


def _dispatch_and_translate_exit(command: str, ctx: ActionToolContext, **kwargs: Any) -> bool:
    should_continue = ctx.slash_ports.dispatch(
        command,
        session=ctx.session,
        console=ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        **kwargs,
    )
    if not should_continue and ctx.request_exit is not None:
        ctx.request_exit()
    return True


def execute_slash_tool(args: dict[str, Any], ctx: ActionToolContext) -> bool:
    if ctx.slash_ports is None:
        raise RuntimeError("slash tool requires slash runtime ports")
    command = str(args.get("command", "")).strip()
    raw_args = args.get("args")
    parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
    full_command = " ".join([command, *parsed_args]) if parsed_args else command
    stripped = full_command.strip()
    if stripped == "/" or not stripped:
        return _dispatch_and_translate_exit(
            stripped or "/",
            ctx,
        )

    parts = stripped.split()
    name = parts[0].lower()
    slash_args = parts[1:]
    if not ctx.slash_ports.command_exists(name):
        return _dispatch_and_translate_exit(
            stripped,
            ctx,
        )

    if stripped in agent_turn_executed_slashes(ctx.session):
        return True

    if _slash_drives_interactive_picker(
        name,
        slash_args,
        session=ctx.session,
        is_tty=ctx.is_tty,
        ports=ctx.slash_ports,
    ) and not exclusive_stdin_active(ctx.session):
        # Hand the picker back to the REPL loop instead of running it against the
        # live prompt: set_auto_command re-submits it as a deterministic turn
        # the loop dispatches with exclusive stdin, so no CPR replies leak in.
        # Do not record a slash history row here — dispatch_slash will record when
        # the queued command runs.
        #
        # Nothing is printed: set_auto_command prefills the prompt and submits it,
        # so the command is already echoed on the input line. Announcing it here
        # as well showed the same command twice before it had even run.
        set_auto_command(ctx.session, stripped)
        return True

    plan = plan_foreground_tool("slash", "slash")
    if not ctx.slash_ports.execution_allowed(
        policy=plan.policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=stripped,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        ctx.session.record(
            "slash",
            stripped,
            ok=False,
            response_text=ctx.slash_ports.format_turn_outcome(stripped, ok=False),
        )
        return True

    # Announce the command unless the input line already did. Exclusive stdin is
    # only reserved for a *literally typed* slash command (see
    # ``turn_needs_exclusive_stdin``), so when it is active the prompt above
    # already shows this command and a banner would repeat it. On every other
    # path the agent resolved free text into a slash — nothing was echoed, and
    # this banner is the only indication of what is about to run.
    if not exclusive_stdin_active(ctx.session):
        ctx.console.print(f"[bold]$ {escape(stripped)}[/bold]")
    _dispatch_and_translate_exit(
        stripped,
        ctx,
        policy_precleared=True,
    )
    agent_turn_executed_slashes(ctx.session).add(stripped)
    return True


def run_slash(*, command: str, args: list[str] | None = None, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"command": command, "args": args or []},
        context,
        execute_slash_tool,
    )


slash_invoke_tool = RegisteredTool(
    name="slash_invoke",
    description=slash_invoke_tool_description(),
    input_schema=slash_invoke_input_schema(),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_slash,
    is_available=lambda sources: capability_available_from_sources(sources, "slash_commands"),
)


__all__ = ["execute_slash_tool", "slash_invoke_tool"]
