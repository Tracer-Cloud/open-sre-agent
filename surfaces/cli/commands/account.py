"""CLI commands for the GitHub-backed personal OpenSRE account."""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from config.constants.account import OPENSRE_APP_URL_DEV
from config.constants.github import GITHUB_CLI_RECOMMENDED_SCOPES
from surfaces.cli.account_auth import (
    AccountAuthError,
    AccountStatus,
    account_status,
    login_account,
    logout_account,
)


def _json_enabled(ctx: click.Context) -> bool:
    return bool(ctx.find_root().obj.get("json", False))


def _dev_enabled(ctx: click.Context, dev: bool) -> bool:
    return bool(dev or ctx.find_root().obj.get("account_dev", False))


def _optional_app_url(*, app_url: str | None, dev: bool) -> str | None:
    if app_url:
        return app_url
    if dev:
        return OPENSRE_APP_URL_DEV
    return None


def _render_status(status: AccountStatus, *, json_output: bool) -> None:
    if json_output:
        click.echo(
            json.dumps(
                {
                    "authenticated": status.authenticated,
                    "detail": status.detail,
                    "account": asdict(status.record) if status.record else None,
                },
                indent=2,
            )
        )
        return
    click.echo(f"Status       : {'authenticated' if status.authenticated else 'not authenticated'}")
    if status.record:
        click.echo(f"GitHub user  : @{status.record.github_username}")
        click.echo(f"Organization : {status.record.organization_id}")
        if status.record.email:
            click.echo(f"Email        : {status.record.email}")
        click.echo(f"LLM provider : {status.record.llm_provider}")
        click.echo(f"LLM model    : {status.record.llm_model}")
        click.echo(f"Expires      : {status.record.token_expires_at}")
    click.echo(f"Detail       : {status.detail}")


@click.group(name="account", invoke_without_command=True)
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.pass_context
def account_command(ctx: click.Context, dev: bool) -> None:
    """Sign in to OpenSRE with GitHub and inspect the local account."""
    ctx.find_root().obj["account_dev"] = dev
    if ctx.invoked_subcommand is None:
        _render_status(
            account_status(app_url=_optional_app_url(app_url=None, dev=dev)),
            json_output=_json_enabled(ctx),
        )


@account_command.command(name="login")
@click.option(
    "--app-url",
    default=None,
    metavar="URL",
    help="OpenSRE webapp origin (or set OPENSRE_APP_URL).",
)
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.option(
    "--browser/--no-browser",
    default=True,
    show_default=True,
    help="Open the GitHub sign-in page automatically.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    default=300.0,
    show_default=True,
    type=click.FloatRange(min=1.0, max=1800.0),
    help="Seconds to wait for the browser callback.",
)
@click.pass_context
def account_login(
    ctx: click.Context,
    app_url: str | None,
    dev: bool,
    browser: bool,
    timeout_seconds: float,
) -> None:
    """Sign in or create a personal account using GitHub only."""
    try:
        result = login_account(
            app_url=_optional_app_url(app_url=app_url, dev=_dev_enabled(ctx, dev)),
            open_browser=browser,
            timeout_seconds=timeout_seconds,
            announce=click.echo,
        )
    except AccountAuthError as exc:
        raise click.ClickException(str(exc)) from exc

    record = result.record
    missing_scopes = sorted(GITHUB_CLI_RECOMMENDED_SCOPES.difference(record.github_scopes))
    if _json_enabled(ctx):
        click.echo(
            json.dumps(
                {
                    "authenticated": True,
                    "account": asdict(record),
                    "missing_recommended_github_scopes": missing_scopes,
                    "warning": result.warning or None,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Signed in as @{record.github_username}.")
    click.echo(f"LLM provider: {record.llm_provider} ({record.llm_model}, hosted by OpenSRE).")
    click.echo("OpenSRE account and GitHub credentials are stored under ~/.opensre.")
    if result.warning:
        click.echo(result.warning)
    if missing_scopes:
        click.echo(
            "GitHub scope warning: add "
            + ", ".join(missing_scopes)
            + " to the Clerk GitHub connection for private repo, organization, and workflow commands."
        )


@account_command.command(name="status")
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.pass_context
def account_status_command(ctx: click.Context, dev: bool) -> None:
    """Validate and display the current personal account."""
    _render_status(
        account_status(app_url=_optional_app_url(app_url=None, dev=_dev_enabled(ctx, dev))),
        json_output=_json_enabled(ctx),
    )


@account_command.command(name="logout")
@click.pass_context
def account_logout(ctx: click.Context) -> None:
    """Revoke the OpenSRE token and clear account-managed GitHub credentials."""
    try:
        result = logout_account()
    except AccountAuthError as exc:
        raise click.ClickException(str(exc)) from exc
    if _json_enabled(ctx):
        click.echo(
            json.dumps(
                {
                    "signed_out": True,
                    "remote_revoked": result.remote_revoked,
                    "detail": result.detail,
                },
                indent=2,
            )
        )
        return
    click.echo(result.detail)


__all__ = ["account_command"]
