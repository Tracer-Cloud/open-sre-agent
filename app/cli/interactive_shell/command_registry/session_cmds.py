"""Slash commands: session control and status (/status, /reset, /clear, /trust, …)."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

import app.cli.interactive_shell.command_registry.repl_data as repl_data
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    render_ready_box,
    repl_table,
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


def _cmd_clear(session: ReplSession, console: Console, _args: list[str]) -> bool:
    console.clear()
    render_ready_box(console, session=session)
    return True


def _cmd_reset(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.interactive_shell.sessions.store import SessionStore

    SessionStore.flush(session)  # close current session file
    session.clear()  # rotate session_id + started_at
    SessionStore.open_session(session)  # open new session file immediately
    console.print(f"[{DIM}]session state cleared — new session started.[/]")
    return True


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


def _cmd_status(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from app.cli.interactive_shell.references.grounding_diagnostics import iter_grounding_sources

    table = repl_table(title="Session status\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    # Show incoming alerts count and most recent age
    if session.incoming_alerts:
        from app.cli.interactive_shell.alert_renderer import time_ago

        most_recent = session.incoming_alerts[-1]
        age_str = time_ago(most_recent.received_at)
        table.add_row("incoming alerts", f"{len(session.incoming_alerts)} (last {age_str})")
    else:
        table.add_row("incoming alerts", "0")

    table.add_row("last investigation", "yes" if session.last_state else "none")
    table.add_row("trust mode", "on" if session.trust_mode else "off")
    table.add_row("reasoning effort", display_reasoning_effort(session.reasoning_effort))
    table.add_row("provider", os.getenv("LLM_PROVIDER", "anthropic"))
    for source in iter_grounding_sources():
        stats = source.stats_fn()
        table.add_row(f"grounding {source.name} cache", source.format_fn(stats))
    acc = session.accumulated_context
    if acc:
        table.add_row("accumulated context", ", ".join(sorted(acc.keys())))
    print_repl_table(console, table)
    return True


def _cmd_cost(session: ReplSession, console: Console, _args: list[str]) -> bool:
    table = repl_table(title="Session cost\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    if session.token_usage:
        inp = session.token_usage.get("input", 0)
        out = session.token_usage.get("output", 0)
        table.add_row("input tokens", f"{inp:,}")
        table.add_row("output tokens", f"{out:,}")
    else:
        table.add_row("token usage", f"[{DIM}]not available (not wired yet)[/]")

    print_repl_table(console, table)
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


def _cmd_context(session: ReplSession, console: Console, _args: list[str]) -> bool:
    if not session.accumulated_context:
        console.print(f"[{DIM}]no infra context accumulated yet.[/]")
        return True

    table = repl_table(title="Accumulated context\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    for k, v in sorted(session.accumulated_context.items()):
        table.add_row(k, escape(str(v)))
    print_repl_table(console, table)
    return True


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


def _cmd_sessions(session: ReplSession, console: Console, _args: list[str]) -> bool:
    from datetime import UTC, datetime

    from app.cli.interactive_shell.sessions.store import SessionStore

    entries = SessionStore.load_recent(20)
    if not entries:
        console.print(f"[{DIM}]No sessions recorded yet.[/]")
        return True

    table = repl_table(title="Recent sessions\n", title_style=BOLD_BRAND)
    table.add_column("#", style="bold", justify="right")
    table.add_column("Session ID", style="bold")
    table.add_column("Started")
    table.add_column("Duration")
    table.add_column("Turns", justify="right")
    table.add_column("Investigations", justify="right")

    for i, entry in enumerate(entries, start=1):
        sid = entry["session_id"]
        short_id = sid[:8] if len(sid) >= 8 else sid
        is_current = sid == session.session_id

        started_raw = entry.get("started_at") or ""
        try:
            started_dt = datetime.fromisoformat(started_raw)
            started_str = started_dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            started_str = started_raw[:16] if started_raw else "—"

        duration_secs = entry.get("duration_secs")
        if is_current:
            try:
                elapsed = int(
                    (
                        datetime.now(UTC) - datetime.fromtimestamp(session.started_at, tz=UTC)
                    ).total_seconds()
                )
                duration_secs = elapsed
            except Exception:
                pass

        if duration_secs is None:
            duration_str = "—"
        elif duration_secs < 60:
            duration_str = f"{duration_secs}s"
        elif duration_secs < 3600:
            duration_str = f"{duration_secs // 60}m {duration_secs % 60}s"
        else:
            h = duration_secs // 3600
            m = (duration_secs % 3600) // 60
            duration_str = f"{h}h {m}m"

        total = entry.get("total_turns")
        investigations = entry.get("investigation_turns")
        sid_col = f"{short_id} [{HIGHLIGHT}](current)[/]" if is_current else short_id

        table.add_row(
            str(i),
            sid_col,
            started_str,
            duration_str,
            str(total) if total is not None else "—",
            str(investigations) if investigations is not None else "—",
        )

    print_repl_table(console, table)
    return True


def _cmd_resume(session: ReplSession, console: Console, args: list[str]) -> bool:
    from app.cli.interactive_shell.sessions.store import SessionStore

    if not args:
        console.print(f"[{DIM}]usage: /resume <session-id-prefix>[/]")
        console.print(f"[{DIM}]run /sessions to list session IDs.[/]")
        return True

    prefix = args[0].strip()
    data = SessionStore.load_session(prefix)

    if data is None:
        # Distinguish "not found" from "ambiguous prefix" using a single store query.
        n = SessionStore.count_prefix_matches(prefix)
        if n > 1:
            console.print(
                f"[{WARNING}]ambiguous prefix '{escape(prefix)}' matches {n} sessions — "
                "use more characters.[/]"
            )
        else:
            console.print(f"[{ERROR}]session '{escape(prefix)}' not found.[/]")
        return True

    messages = data.get("cli_agent_messages") or []
    context = data.get("accumulated_context") or {}
    has_snapshot = data.get("has_snapshot", False)
    sid = data.get("session_id", prefix)
    short_id = sid[:8] if len(sid) >= 8 else sid

    if not messages and not context:
        console.print(
            f"[{DIM}]session {short_id} has no conversation to resume "
            "(no chat turns or context found).[/]"
        )
        if not data.get("turn_details") and not has_snapshot:
            console.print(
                f"[{DIM}]tip: turn_detail records are only written when prompt logging is enabled.[/]"
            )
        return True

    # Warn if replacing an active conversation — cli_agent_messages is fully replaced.
    existing = session.cli_agent_messages
    if existing:
        console.print(
            f"[{WARNING}]current session has {len(existing)} messages — "
            "they will be replaced by the resumed context.[/]"
        )

    session.cli_agent_messages = list(messages)
    session.accumulated_context = dict(context)

    source = "snapshot" if has_snapshot else "turn records"
    console.print(
        f"[{HIGHLIGHT}]resumed session {short_id}[/] "
        f"[{DIM}]({len(messages)} messages from {source})[/]"
    )
    if context:
        console.print(
            f"[{DIM}]accumulated context restored:[/] "
            + ", ".join(f"{k}={escape(str(v))}" for k, v in sorted(context.items()))
        )
    console.print(f"[{DIM}]conversation context loaded — continue asking questions.[/]")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/clear", "Clear the screen and re-render the banner.", _cmd_clear),
    SlashCommand("/reset", "Clear session state.", _cmd_reset, notes=("Trust mode is preserved.",)),
    SlashCommand(
        "/trust",
        "Manage trust mode.",
        _cmd_trust,
        usage=("/trust", "/trust on", "/trust off"),
        notes=("In a TTY, bare /trust opens an interactive menu.",),
        first_arg_completions=_TRUST_FIRST_ARGS,
        execution_tier=ExecutionTier.EXEMPT,
    ),
    SlashCommand("/status", "Show session status.", _cmd_status),
    SlashCommand("/context", "Show accumulated infra context.", _cmd_context),
    SlashCommand("/cost", "Show token usage and session cost.", _cmd_cost),
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
    SlashCommand("/sessions", "List recent REPL sessions.", _cmd_sessions),
    SlashCommand(
        "/resume",
        "Resume a previous session by restoring its conversation context.",
        _cmd_resume,
        usage=("/resume <session-id-prefix>",),
        notes=(
            "Restores cli_agent_messages and accumulated infra context from the chosen session.",
            "The first 8 characters of a session ID (shown by /sessions) are enough.",
            "Replaces the current session's LLM conversation context; warns if messages exist.",
        ),
    ),
]

__all__ = ["COMMANDS"]
