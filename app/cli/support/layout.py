"""Rich landing and help renderers for the OpenSRE CLI."""

from __future__ import annotations

from collections.abc import Sequence

import click
from rich.console import Console
from rich.text import Text

from app.cli.interactive_shell.ui.banner import build_ready_panel
from app.cli.interactive_shell.ui.theme import BRAND, DIM, TEXT

_LANDING_EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        'opensre "investigate high latency in checkout-api"',
        "Start the interactive agent with a prompt",
    ),
    ("opensre onboard", "Configure LLM provider and integrations"),
    ("opensre investigate -i alert.json", "Run RCA against an alert payload"),
    ("opensre deploy ec2", "Deploy investigation server on AWS EC2"),
    ("opensre remote --url <ip> health", "Check a remote deployed agent"),
    ("opensre remote ops status", "Inspect hosted service status (Railway)"),
    ("opensre tests", "Browse and run inventoried tests"),
    ("opensre integrations list", "Show configured integrations"),
    ("opensre guardrails rules", "List configured guardrail rules"),
    ("opensre health", "Check integration and agent setup status"),
    ("opensre doctor", "Run a full environment diagnostic"),
    ("opensre update", "Update to the latest version"),
    ("opensre version", "Print detailed version, Python and OS info"),
)


def _commands_from_group(group: click.Group) -> tuple[tuple[str, str], ...]:
    ctx = click.Context(group)
    rows = []
    for name in group.list_commands(ctx):
        cmd = group.get_command(ctx, name)
        if cmd is not None and not cmd.hidden:
            rows.append((name, cmd.get_short_help_str(limit=200)))
    return tuple(rows)


def _options_from_command(command: click.Command) -> tuple[tuple[str, str], ...]:
    ctx = click.Context(command)
    rows: list[tuple[str, str]] = []
    for param in command.get_params(ctx):
        if getattr(param, "hidden", False):
            continue
        if not isinstance(param, click.Option):
            continue
        record = param.get_help_record(ctx)
        if record is not None:
            rows.append(record)
    return tuple(rows)


def _render_usage(console: Console) -> None:
    console.print(
        Text.assemble(
            ("  Usage: "),
            ("opensre", f"bold {TEXT}"),
            (" [OPTIONS] [COMMAND] [ARGS]..."),
        )
    )
    console.print(
        Text.assemble(
            ("  ", ""),
            ("No COMMAND", DIM),
            (": start the interactive shell when stdin/stdout are TTYs.", DIM),
        )
    )


def _render_rows(
    console: Console,
    *,
    title: str,
    rows: Sequence[tuple[str, str]],
    width: int | None = None,
) -> None:
    effective_width = (
        width + 2 if width is not None else max((len(label) for label, _ in rows), default=0) + 2
    )
    console.print(Text.assemble((f"  {title}:", f"bold {TEXT}")))
    for label, description in rows:
        console.print(
            Text.assemble(
                ("    ", ""),
                (f"{label:<{effective_width}}", f"bold {BRAND}"),
                description,
            )
        )


def render_help(group: click.Group) -> None:
    """Render the root help view, deriving the command list from the live Click group."""
    console = Console(highlight=False)
    commands = _commands_from_group(group)
    options = _options_from_command(group)
    console.print()
    _render_usage(console)
    console.print()
    _render_rows(console, title="Commands", rows=commands, width=16)
    console.print()
    _render_rows(console, title="Options", rows=options)
    console.print()


def render_landing(group: click.Group) -> None:
    """Render the root landing page shown with no subcommand."""
    console = Console(highlight=False)
    options = _options_from_command(group)
    console.print()
    console.print(build_ready_panel(console))
    console.print(
        Text.assemble(
            ("  ", ""),
            "open-source SRE agent for automated incident investigation and root cause analysis",
        )
    )
    console.print()
    _render_usage(console)
    console.print()
    _render_rows(console, title="Quick start", rows=_LANDING_EXAMPLES, width=42)
    console.print()
    _render_rows(console, title="Options", rows=options)
    console.print()


class RichGroup(click.Group):
    """Click group with a custom Rich-powered help screen."""

    def format_help(self, ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        assert isinstance(ctx.command, click.Group)
        render_help(ctx.command)
