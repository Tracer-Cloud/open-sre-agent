"""Slash commands for /list, /integrations, and /mcp."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.cli_parity import run_cli_command
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.config.tool_catalog import (
    build_tool_catalog,
    format_tool_catalog_text,
)
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    MCP_INTEGRATION_SERVICES,
    WARNING,
    render_integrations_table,
    render_mcp_table,
    render_models_table,
    repl_table,
)
from app.cli.interactive_shell.ui.choice_menu import (
    CRUMB_SEP,
    prepare_repl_output_line,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from app.cli.interactive_shell.ui.rendering import (
    _repl_table_width,
    print_repl_table,
    repl_print,
)

_ROOT_LIST = "/list"
_ROOT_INTEGRATIONS = "/integrations"
_ROOT_MCP = "/mcp"


def _configured_service_choices() -> list[tuple[str, str]]:
    """Build picker choices from configured integrations (no live verification)."""
    return [(name, name) for name in repl_data.configured_integration_names()]


def _mcp_service_choices() -> list[tuple[str, str]]:
    names = [
        name
        for name in repl_data.configured_integration_names()
        if name in MCP_INTEGRATION_SERVICES
    ]
    return [(name, name) for name in names]


def _render_integration_show(console: Console, service: str) -> bool:
    """Verify and print one integration. Returns False when the service is unknown."""
    normalized = service.strip().lower()
    configured = set(repl_data.configured_integration_names())
    if normalized not in configured:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

    prepare_repl_output_line()
    with console.status(
        f"[{DIM}]Verifying {escape(normalized)}…[/]",
        spinner="dots",
    ):
        match = repl_data.verify_integration(normalized)
    if match is None:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

    width = _repl_table_width(console)
    table = repl_table(
        title=f"Integration: {normalized}",
        title_style=BOLD_BRAND,
        show_header=False,
        width=width,
    )
    table.add_column("key", style="bold", no_wrap=True)
    value_width = max(20, width - 20)
    table.add_column("value", overflow="fold", max_width=value_width)
    for key, value in match.items():
        table.add_row(escape(key), escape(str(value)))
    print_repl_table(console, table)
    return True


def _interactive_list_menu(_session: ReplSession, console: Console) -> bool:
    while True:
        sub = repl_choose_one(
            title="list",
            breadcrumb=_ROOT_LIST,
            choices=[
                ("integrations", "/list integrations"),
                ("models", "/list models"),
                ("mcp", "/list mcp"),
                ("tools", "/list tools"),
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
                repl_print(console, "[dim]no tools registered.[/dim]")
            else:
                repl_print(console, format_tool_catalog_text(catalog), markup=False)
        repl_section_break(console)


def _cmd_integrations(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_integrations_menu(session, console)

    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        prepare_repl_output_line()
        with console.status(f"[{DIM}]Verifying integrations…[/]", spinner="dots"):
            results = repl_data.load_verified_integrations()
        render_integrations_table(console, results)
        return True

    if sub == "verify":
        prepare_repl_output_line()
        with console.status(f"[{DIM}]Verifying integrations…[/]", spinner="dots"):
            results = repl_data.load_verified_integrations()
        render_integrations_table(console, results)
        failed = [r for r in results if r.get("status") in ("failed", "missing")]
        if failed:
            repl_print(console, f"[{WARNING}]{len(failed)} integration(s) need attention.[/]")
        else:
            repl_print(console, f"[{HIGHLIGHT}]all integrations ok.[/]")
        return True

    if sub == "setup":
        return run_cli_command(console, ["integrations", "setup", *args[1:]])

    if sub == "remove":
        return run_cli_command(console, ["integrations", "remove", *args[1:]])

    if sub == "show":
        if len(args) < 2:
            repl_print(console, f"[{DIM}]usage:[/] /integrations show <service>")
            session.mark_latest(ok=False, kind="slash")
            return True
        if not _render_integration_show(console, args[1]):
            session.mark_latest(ok=False, kind="slash")
        return True

    repl_print(
        console,
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/integrations list[/bold], [bold]/integrations verify[/bold], "
        "or [bold]/integrations show <service>[/bold])",
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
                ("list", "/integrations list"),
                ("verify", "/integrations verify"),
                ("show", "/integrations show <service>"),
                ("setup", "/integrations setup <service>"),
                ("remove", "/integrations remove <service>"),
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
            choices = _configured_service_choices()
            if not choices:
                repl_print(console, "[dim]no integrations in store to show.[/dim]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="service",
                    breadcrumb=f"{root}{CRUMB_SEP}show",
                    choices=choices,
                )
                if svc and _render_integration_show(console, svc):
                    show_section_break = True
        elif sub == "remove":
            choices = _configured_service_choices()
            if not choices:
                repl_print(console, "[dim]no integrations in store to remove.[/dim]")
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
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
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
                ("list", "/mcp list"),
                ("connect", "/mcp connect <server>"),
                ("disconnect", "/mcp disconnect <server>"),
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
            console.print(f"[{DIM}]no tools registered.[/]")
            return True
        console.print(format_tool_catalog_text(catalog), markup=False)
        return True

    if sub and sub not in ("", "all"):
        console.print(
            f"[{ERROR}]unknown list target:[/] {escape(sub)}  "
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
        "Browse integrations, MCP servers, models, and tools.",
        _cmd_list,
        usage=("/list", "/list integrations", "/list models", "/list mcp", "/list tools"),
        notes=("In a TTY, bare /list opens an interactive menu.",),
        first_arg_completions=_LIST_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/integrations",
        "Manage integrations.",
        _cmd_integrations,
        usage=(
            "/integrations",
            "/integrations list",
            "/integrations verify",
            "/integrations show <service>",
        ),
        notes=("In a TTY, bare /integrations opens an interactive menu.",),
        first_arg_completions=_INTEGRATIONS_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/mcp",
        "Manage MCP servers.",
        _cmd_mcp,
        usage=("/mcp", "/mcp list", "/mcp connect", "/mcp disconnect"),
        notes=("In a TTY, bare /mcp opens an interactive menu.",),
        first_arg_completions=_MCP_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
]

__all__ = ["COMMANDS"]
