"""Slash commands: session settings (/trust, /effort, /verbose, /compact)."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.command_registry.repl_data as repl_data
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    resolve_provider_models,
)
from app.cli.interactive_shell.ui.choice_menu import (
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from app.llm_reasoning_effort import (
    REASONING_EFFORT_OPTIONS,
    describe_reasoning_effort_default,
    display_reasoning_effort,
    parse_reasoning_effort,
    provider_supports_reasoning_effort,
)

_TRUST_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable trust mode (skip approval prompts)"),
    ("off", "disable trust mode"),
)

_VERBOSE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable verbose logging"),
    ("off", "disable verbose logging"),
)

_EFFORT_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("low", "favor speed and lower reasoning cost"),
    ("medium", "balanced reasoning effort"),
    ("high", "favor more thorough reasoning"),
    ("xhigh", "favor deepest supported reasoning"),
    ("max", "alias for xhigh"),
)


def _interactive_trust_menu(session: ReplSession, console: Console) -> bool:
    while True:
        mode = repl_choose_one(
            title="trust",
            breadcrumb="/trust",
            choices=[("on", "on"), ("off", "off"), ("done", "done")],
        )
        if mode is None or mode == "done":
            return True
        _cmd_trust(session, console, [mode])
        repl_section_break(console)


def _cmd_trust(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_trust_menu(session, console)

    if args and args[0].lower() in ("off", "false", "disable"):
        session.trust_mode = False
        console.print(f"[{DIM}]trust mode off[/]")
    else:
        session.trust_mode = True
        console.print(f"[{WARNING}]trust mode on[/] — future approval prompts will be skipped")
    return True


def _cmd_effort(session: ReplSession, console: Console, args: list[str]) -> bool:
    settings = repl_data.load_llm_settings()
    provider = str(getattr(settings, "provider", os.getenv("LLM_PROVIDER", "anthropic")))
    reasoning_model = ""
    if settings is not None:
        reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    supported_values = ", ".join(REASONING_EFFORT_OPTIONS)

    if not args:
        console.print(
            f"[{HIGHLIGHT}]reasoning effort:[/] {display_reasoning_effort(session.reasoning_effort)}"
        )
        console.print(
            f"[{DIM}]default config:[/] "
            f"{escape(describe_reasoning_effort_default(provider, reasoning_model))}"
        )
        console.print(f"[{DIM}]usage:[/] /effort <{supported_values}>")
        if not provider_supports_reasoning_effort(provider):
            console.print(
                f"[{DIM}]current provider {provider} ignores this setting; "
                "switch to openai or codex to use it.[/]"
            )
        return True

    effort = parse_reasoning_effort(args[0])
    if effort is None:
        console.print(
            f"[{ERROR}]unknown reasoning effort:[/] {escape(args[0])} "
            f"[{DIM}](choices: {supported_values})[/]"
        )
        session.mark_latest(ok=False, kind="slash")
        return True

    session.reasoning_effort = effort
    console.print(f"[{HIGHLIGHT}]reasoning effort set to:[/] {display_reasoning_effort(effort)}")
    if not provider_supports_reasoning_effort(provider):
        console.print(
            f"[{DIM}]current provider {provider} ignores this setting; "
            "switch to openai or codex to use it.[/]"
        )
    elif effort in {"xhigh", "max"}:
        console.print(
            f"[{DIM}]xhigh/max work best with newer GPT-5 or Codex models; "
            "older reasoning models may reject them.[/]"
        )
    return True


def _interactive_verbose_menu(_session: ReplSession, console: Console) -> bool:
    while True:
        mode = repl_choose_one(
            title="verbose",
            breadcrumb="/verbose",
            choices=[("on", "on"), ("off", "off"), ("done", "done")],
        )
        if mode is None or mode == "done":
            return True
        _cmd_verbose(_session, console, [mode])
        repl_section_break(console)


def _cmd_verbose(_session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_verbose_menu(_session, console)

    if args and args[0].lower() in ("off", "false", "0", "disable"):
        os.environ.pop("TRACER_VERBOSE", None)
        console.print(f"[{DIM}]verbose logging off[/]")
    else:
        os.environ["TRACER_VERBOSE"] = "1"
        console.print(f"[{WARNING}]verbose logging on[/]")
    return True


def _cmd_compact(session: ReplSession, console: Console, _args: list[str]) -> bool:
    before = len(session.history)
    if before > 20:
        session.history = session.history[-20:]
        console.print(f"[{DIM}]compacted: kept last 20 of {before} entries.[/]")
    else:
        console.print(f"[{DIM}]nothing to compact ({before} entries, limit is 20).[/]")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/trust",
        "Manage trust mode.",
        _cmd_trust,
        usage=("/trust", "/trust on", "/trust off"),
        notes=("In a TTY, bare /trust opens an interactive menu.",),
        first_arg_completions=_TRUST_FIRST_ARGS,
        execution_tier=ExecutionTier.EXEMPT,
    ),
    SlashCommand(
        "/effort",
        "Set REPL reasoning effort.",
        _cmd_effort,
        usage=("/effort <low|medium|high|xhigh|max>",),
        first_arg_completions=_EFFORT_FIRST_ARGS,
    ),
    SlashCommand(
        "/verbose",
        "Manage verbose logging.",
        _cmd_verbose,
        usage=("/verbose", "/verbose on", "/verbose off"),
        notes=("In a TTY, bare /verbose opens an interactive menu.",),
        first_arg_completions=_VERBOSE_FIRST_ARGS,
    ),
    SlashCommand("/compact", "Trim old session history to free memory.", _cmd_compact),
]

__all__ = ["COMMANDS", "_TRUST_FIRST_ARGS", "_VERBOSE_FIRST_ARGS", "_EFFORT_FIRST_ARGS"]
