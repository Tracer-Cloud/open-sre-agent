"""``opensre runbook`` CLI group — manage local diagnosis runbooks."""

from __future__ import annotations

from pathlib import Path

import click

from app.runbooks.store import (
    RunbookValidationError,
    _runbook_dir,
    load_all,
    remove,
    save,
)


@click.group(name="runbook")
def runbook() -> None:
    """Manage local runbooks that ground diagnosis remediation steps."""


@runbook.command("add")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def runbook_add(path: Path) -> None:
    """Copy a markdown runbook into ~/.config/opensre/runbooks/."""
    try:
        stored = save(path)
    except RunbookValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"✓ Saved runbook '{stored.slug}' to {stored.path}")


@runbook.command("list")
def runbook_list() -> None:
    """List runbooks currently in the local store."""
    runbooks = load_all()
    if not runbooks:
        click.echo(f"No runbooks found in {_runbook_dir()}")
        return
    for rb in runbooks:
        service = rb.service or "-"
        triggers = ", ".join(rb.triggers)
        click.echo(f"{rb.slug}  service={service}  triggers=[{triggers}]")


@runbook.command("remove")
@click.argument("slug")
def runbook_remove(slug: str) -> None:
    """Delete a runbook by slug (the filename without .md)."""
    if remove(slug):
        click.echo(f"✓ Removed runbook '{slug}'")
        return
    raise click.ClickException(f"no runbook with slug '{slug}' in {_runbook_dir()}")
