"""Manage trusted runbook sources."""

from __future__ import annotations

import click

from config.runbook_sources import (
    RunbookSourceConfig,
    RunbookSourceConfigError,
    add_runbook_source,
    load_runbook_sources,
    remove_runbook_source,
)
from infrastructure.terminal.theme import GLYPH_SUCCESS


@click.group(name="runbooks")
def runbooks_command() -> None:
    """Manage organization-owned runbook sources."""


@runbooks_command.group(name="source")
def source_command() -> None:
    """Add, inspect, and remove trusted runbook sources."""


@source_command.command(name="add")
@click.argument("provider", type=click.Choice(("github",), case_sensitive=False))
@click.option("--name", required=True, help="Stable name for this runbook source.")
@click.option("--repo", "repository", required=True, help="GitHub owner/repository.")
@click.option("--ref", default="main", show_default=True, help="Branch, tag, or commit.")
@click.option("--manifest", default="", help="Optional repository-relative YAML manifest.")
def source_add(
    provider: str,
    name: str,
    repository: str,
    ref: str,
    manifest: str,
) -> None:
    """Register a trusted runbook source."""
    try:
        source = RunbookSourceConfig(
            name=name,
            provider=provider,
            repository=repository,
            ref=ref,
            manifest=manifest,
        )
        add_runbook_source(source)
    except (RunbookSourceConfigError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{GLYPH_SUCCESS} Added runbook source {source.name}")


@source_command.command(name="list")
def source_list() -> None:
    """List configured runbook sources."""
    try:
        sources = load_runbook_sources()
    except RunbookSourceConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if not sources:
        click.echo("No runbook sources configured.")
        return
    for source in sources:
        manifest = source.manifest or "(explicit URLs only)"
        click.echo(
            f"{source.name}: {source.provider} {source.repository}@{source.ref} "
            f"manifest={manifest}"
        )


@source_command.command(name="remove")
@click.argument("name")
def source_remove(name: str) -> None:
    """Remove a configured runbook source."""
    try:
        removed = remove_runbook_source(name)
    except RunbookSourceConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    if not removed:
        raise click.ClickException(f"Runbook source {name!r} was not found")
    click.echo(f"{GLYPH_SUCCESS} Removed runbook source {name}")


__all__ = ["runbooks_command"]
