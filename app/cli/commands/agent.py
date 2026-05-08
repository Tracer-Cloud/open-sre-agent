"""Local agent fleet management CLI commands.

Phase-0 skeleton for the ``monitor-local-agents`` initiative. The subcommands
intentionally print a placeholder message; real functionality lands in later
phases (registry persistence, psutil probing, slash-command surfacing in the
interactive shell).
"""

from __future__ import annotations

import click

_NOT_IMPLEMENTED_MESSAGE = "not implemented yet"


@click.group(name="agents")
def agents() -> None:
    """Manage the local AI agent fleet (Claude Code, Cursor, Aider, ...)."""


@agents.command(name="list")
def list_agents() -> None:
    """List tracked local agents."""
    click.echo(_NOT_IMPLEMENTED_MESSAGE)


@agents.command(name="register")
def register_agent() -> None:
    """Start tracking a local agent process."""
    click.echo(_NOT_IMPLEMENTED_MESSAGE)


@agents.command(name="forget")
def forget_agent() -> None:
    """Stop tracking a local agent process."""
    click.echo(_NOT_IMPLEMENTED_MESSAGE)
