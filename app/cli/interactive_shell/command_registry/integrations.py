"""Slash commands for /list, /integrations, and /mcp."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.rendering import (
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    repl_table,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD, TERMINAL_ERROR


def _cmd_integrations(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        render_integrations_table(console, repl_data.load_verified_integrations())
        return True

    if sub == "verify":
        results = repl_data.load_verified_integrations()
        render_integrations_table(console, results)
        failed = [r for r in results if r.get("status") in ("failed", "missing")]
        if failed:
            console.print(f"[yellow]{len(failed)} integration(s) need attention.[/yellow]")
        else:
            console.print("[green]all integrations ok.[/green]")
        return True

    if sub == "show":
        if len(args) < 2:
            console.print("[dim]usage:[/dim] /integrations show <service>")
            return True
        service = args[1].lower()
        results = repl_data.load_verified_integrations()
        match = next((r for r in results if r.get("service") == service), None)
        if match is None:
            console.print(f"[{TERMINAL_ERROR}]service not found:[/] {escape(service)}")
            return True
        table = repl_table(
            title=f"Integration: {service}",
            title_style=TERMINAL_ACCENT_BOLD,
            show_header=False,
        )
        table.add_column("key", style="bold")
        table.add_column("value")
        for k, v in match.items():
            table.add_row(k, str(v))
        console.print(table)
        return True

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/integrations list[/bold], [bold]/integrations verify[/bold], "
        "or [bold]/integrations show <service>[/bold])"
    )
    return True


def _cmd_mcp(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        render_mcp_table(console, repl_data.load_verified_integrations())
        return True

    if sub == "connect":
        console.print(
            "[dim]to connect an MCP server, run:[/dim] [bold]opensre integrations setup[/bold]"
        )
        return True

    if sub == "disconnect":
        console.print(
            "[dim]to remove an MCP server, run:[/dim] [bold]opensre integrations remove <service>[/bold]"
        )
        return True

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/mcp list[/bold], [bold]/mcp connect[/bold], or [bold]/mcp disconnect[/bold])"
    )
    return True


def _cmd_list(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "").strip()

    if sub in ("integrations", "integration", "int"):
        render_integrations_table(console, repl_data.load_verified_integrations())
        return True

    if sub in ("mcp", "mcps"):
        render_mcp_table(console, repl_data.load_verified_integrations())
        return True

    if sub in ("models", "model", "llm", "llms"):
        render_models_table(console, repl_data.load_llm_settings())
        return True

    if sub and sub not in ("", "all"):
        console.print(
            f"[{TERMINAL_ERROR}]unknown list target:[/] {escape(sub)}  "
            "(try [bold]/list integrations[/bold], [bold]/list models[/bold], "
            "or [bold]/list mcp[/bold])"
        )
        return True

    results = repl_data.load_verified_integrations()
    render_integrations_table(console, results)
    render_mcp_table(console, results)
    render_models_table(console, repl_data.load_llm_settings())
    return True


_LIST_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("integrations", "alert-source integrations"),
    ("models", "active LLM models"),
    ("mcp", "connected MCP servers"),
)

_INTEGRATIONS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list all configured integrations"),
    ("verify", "run health checks on all integrations"),
    ("show", "show details for a single integration"),
)

_MCP_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list connected MCP servers"),
    ("connect", "add an MCP server via opensre integrations setup"),
    ("disconnect", "remove an MCP server"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/list",
        "list integrations, MCP servers, and the active LLM connection "
        "('/list integrations', '/list models', '/list mcp')",
        _cmd_list,
        first_arg_completions=_LIST_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/integrations",
        "manage integrations ('/integrations list', '/integrations verify', "
        "'/integrations show <service>')",
        _cmd_integrations,
        first_arg_completions=_INTEGRATIONS_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/mcp",
        "manage MCP servers ('/mcp list', '/mcp connect', '/mcp disconnect')",
        _cmd_mcp,
        first_arg_completions=_MCP_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
]

__all__ = ["COMMANDS"]
