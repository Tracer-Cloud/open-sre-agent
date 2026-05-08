"""Slash commands for /list, /integrations, and /mcp."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.cli_parity import run_cli_command
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.rendering import (
    MCP_INTEGRATION_SERVICES,
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    repl_table,
)
from app.cli.interactive_shell.repl_choice_menu import (
    CRUMB_SEP,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD, TERMINAL_ERROR
from app.cli.interactive_shell.tool_catalog import build_tool_catalog, format_tool_catalog_text

_ROOT_LIST = "/list"
_ROOT_INTEGRATIONS = "/integrations"
_ROOT_MCP = "/mcp"


def _verified_service_choices() -> list[tuple[str, str]]:
    rows = repl_data.load_verified_integrations()
    names = sorted({str(r.get("service", "")) for r in rows if r.get("service")})
    return [(n, n) for n in names]


def _mcp_service_choices() -> list[tuple[str, str]]:
    rows = repl_data.load_verified_integrations()
    names = sorted(
        {str(r.get("service", "")) for r in rows if r.get("service") in MCP_INTEGRATION_SERVICES}
    )
    return [(n, n) for n in names if n]


def _interactive_list_menu(_session: ReplSession, console: Console) -> bool:
    while True:
        sub = repl_choose_one(
            title="list",
            breadcrumb=_ROOT_LIST,
            choices=[
                ("integrations", "integrations"),
                ("models", "models"),
                ("mcp", "mcp"),
                ("tools", "tools"),
                ("all", "all"),
                ("done", "done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        if sub == "integrations":
            results = repl_data.load_verified_integrations()
            render_integrations_table(console, results)
        elif sub == "mcp":
            results = repl_data.load_verified_integrations()
            render_mcp_table(console, results)
        elif sub == "models":
            render_models_table(console, repl_data.load_llm_settings())
        elif sub == "all":
            results = repl_data.load_verified_integrations()
            render_integrations_table(console, results)
            render_mcp_table(console, results)
            render_models_table(console, repl_data.load_llm_settings())
        elif sub == "tools":
            catalog = build_tool_catalog()
            if not catalog:
                console.print("[dim]no tools registered.[/dim]")
            else:
                console.print(format_tool_catalog_text(catalog), markup=False)
        repl_section_break(console)


def _cmd_integrations(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_integrations_menu(session, console)

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

    if sub == "setup":
        return run_cli_command(console, ["integrations", "setup", *args[1:]])

    if sub == "remove":
        return run_cli_command(console, ["integrations", "remove", *args[1:]])

    if sub == "show":
        if len(args) < 2:
            console.print("[dim]usage:[/dim] /integrations show <service>")
            session.mark_latest(ok=False, kind="slash")
            return True
        service = args[1].lower()
        results = repl_data.load_verified_integrations()
        match = next((r for r in results if r.get("service") == service), None)
        if match is None:
            console.print(f"[{TERMINAL_ERROR}]service not found:[/] {escape(service)}")
            session.mark_latest(ok=False, kind="slash")
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
    session.mark_latest(ok=False, kind="slash")
    return True


def _interactive_integrations_menu(session: ReplSession, console: Console) -> bool:
    root = _ROOT_INTEGRATIONS
    while True:
        sub = repl_choose_one(
            title="integrations",
            breadcrumb=root,
            choices=[
                ("list", "list"),
                ("verify", "verify"),
                ("show", "show"),
                ("setup", "setup"),
                ("remove", "remove"),
                ("done", "done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        show_section_break = False
        if sub == "list":
            _cmd_integrations(session, console, ["list"])
            show_section_break = True
        elif sub == "verify":
            _cmd_integrations(session, console, ["verify"])
            show_section_break = True
        elif sub == "setup":
            _cmd_integrations(session, console, ["setup"])
            show_section_break = True
        elif sub == "show":
            choices = _verified_service_choices()
            if not choices:
                console.print("[dim]no integrations in store to show.[/dim]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="service",
                    breadcrumb=f"{root}{CRUMB_SEP}show",
                    choices=choices,
                )
                if svc:
                    _cmd_integrations(session, console, ["show", svc])
                    show_section_break = True
        elif sub == "remove":
            choices = _verified_service_choices()
            if not choices:
                console.print("[dim]no integrations in store to remove.[/dim]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="service",
                    breadcrumb=f"{root}{CRUMB_SEP}remove",
                    choices=choices,
                )
                if svc:
                    _cmd_integrations(session, console, ["remove", svc])
                    show_section_break = True
        if show_section_break:
            repl_section_break(console)


def _cmd_mcp(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_mcp_menu(session, console)

    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        render_mcp_table(console, repl_data.load_verified_integrations())
        return True

    if sub == "connect":
        return run_cli_command(console, ["integrations", "setup", *args[1:]])

    if sub == "disconnect":
        return run_cli_command(console, ["integrations", "remove", *args[1:]])

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/mcp list[/bold], [bold]/mcp connect[/bold], or [bold]/mcp disconnect[/bold])"
    )
    return True


def _interactive_mcp_menu(session: ReplSession, console: Console) -> bool:
    root = _ROOT_MCP
    while True:
        sub = repl_choose_one(
            title="mcp",
            breadcrumb=root,
            choices=[
                ("list", "list"),
                ("connect", "connect"),
                ("disconnect", "disconnect"),
                ("done", "done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        show_section_break = False
        if sub == "list":
            _cmd_mcp(session, console, ["list"])
            show_section_break = True
        elif sub == "connect":
            _cmd_mcp(session, console, ["connect"])
            show_section_break = True
        elif sub == "disconnect":
            choices = _mcp_service_choices()
            if not choices:
                console.print("[dim]no MCP servers configured.[/dim]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="server",
                    breadcrumb=f"{root}{CRUMB_SEP}disconnect",
                    choices=choices,
                )
                if svc:
                    _cmd_mcp(session, console, ["disconnect", svc])
                    show_section_break = True
        if show_section_break:
            repl_section_break(console)


def _cmd_list(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_list_menu(session, console)

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

    if sub in ("tools", "tool"):
        catalog = build_tool_catalog()
        if not catalog:
            console.print("[dim]no tools registered.[/dim]")
            return True
        console.print(format_tool_catalog_text(catalog), markup=False)
        return True

    if sub and sub not in ("", "all"):
        console.print(
            f"[{TERMINAL_ERROR}]unknown list target:[/] {escape(sub)}  "
            "(try [bold]/list integrations[/bold], [bold]/list models[/bold], "
            "[bold]/list mcp[/bold], or [bold]/list tools[/bold])"
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
    ("tools", "registered tools (investigation + chat surfaces)"),
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
        "list integrations, MCP, tools, LLM (TTY: bare '/list' opens inline menu; "
        "else '/list integrations', '/list models', '/list mcp', '/list tools')",
        _cmd_list,
        first_arg_completions=_LIST_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/integrations",
        "manage integrations (TTY: bare '/integrations' opens menu; else "
        "'/integrations list', '/integrations verify', '/integrations show <service>')",
        _cmd_integrations,
        first_arg_completions=_INTEGRATIONS_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/mcp",
        "manage MCP servers (TTY: bare '/mcp' opens menu; else "
        "'/mcp list', '/mcp connect', '/mcp disconnect')",
        _cmd_mcp,
        first_arg_completions=_MCP_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
]

__all__ = ["COMMANDS"]
