"""Local agent fleet management CLI commands."""

from __future__ import annotations

import click
from rich.console import Console

from app.agents.discovery import registered_and_discovered_agents
from app.cli.interactive_shell.agents_view import render_agents_table

_NOT_IMPLEMENTED_MESSAGE = "not implemented yet"


@click.group(name="agents")
def agents() -> None:
    """Manage the local AI agent fleet (Claude Code, Cursor, Aider, ...)."""


@agents.command(name="list")
def list_agents() -> None:
    """List registered and auto-discovered local agents."""
    Console().print(render_agents_table(registered_and_discovered_agents()))


@agents.command(name="register")
def register_agent() -> None:
    """Start tracking a local agent process."""
    click.echo(_NOT_IMPLEMENTED_MESSAGE)


@agents.command(name="forget")
def forget_agent() -> None:
    """Stop tracking a local agent process."""
    click.echo(_NOT_IMPLEMENTED_MESSAGE)
