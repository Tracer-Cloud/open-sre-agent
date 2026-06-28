"""CLI commands for LLM provider authentication."""

from __future__ import annotations

import sys
import webbrowser
from collections.abc import Iterable

import click

from cli.llm_auth.providers import ProviderAuthProfile, iter_auth_profiles, resolve_auth_profile
from cli.llm_auth.service import (
    AuthSetupError,
    configure_api_key_provider,
    configure_cli_subscription_provider,
    logout_provider,
    provider_status,
    verify_provider,
)


def _provider_choices() -> str:
    return ", ".join(profile.name for profile in iter_auth_profiles())


def _resolve_or_raise(provider: str) -> ProviderAuthProfile:
    try:
        return resolve_auth_profile(provider)
    except KeyError as exc:
        raise click.UsageError(
            f"Unknown auth provider '{provider}'. Choose one of: {_provider_choices()}."
        ) from exc


def _prompt_provider() -> ProviderAuthProfile:
    click.echo("Subscription logins:")
    for profile in iter_auth_profiles():
        if profile.kind == "cli_subscription":
            click.echo(f"  {profile.name:<10} {profile.label}")
    click.echo("API-key providers:")
    api_key_names = [profile.name for profile in iter_auth_profiles() if profile.kind == "api_key"]
    click.echo(f"  {', '.join(api_key_names)}")
    provider = click.prompt("Provider", type=str).strip()
    return _resolve_or_raise(provider)


def _maybe_open_setup_page(profile: ProviderAuthProfile, *, enabled: bool) -> None:
    if not enabled or not profile.setup_url:
        return
    if not sys.stdin.isatty():
        click.echo(f"Setup page: {profile.setup_url}")
        return
    if click.confirm(f"Open {profile.label} setup page in your browser?", default=True):
        webbrowser.open(profile.setup_url)


def _status_lines(providers: Iterable[ProviderAuthProfile]) -> list[str]:
    lines = [f"{'Provider':<14} {'Status':<8} {'Source':<11} Detail"]
    for profile in providers:
        status = provider_status(profile.name)
        state = "stale" if status.stale else "ok" if status.authenticated else "missing"
        lines.append(f"{profile.name:<14} {state:<8} {status.source:<11} {status.detail}")
    return lines


@click.group(name="auth", invoke_without_command=True)
@click.pass_context
def auth_command(ctx: click.Context) -> None:
    """Log in to LLM providers and inspect local auth state."""
    if ctx.invoked_subcommand is None:
        for line in _status_lines(iter_auth_profiles()):
            click.echo(line)


@auth_command.command(name="login")
@click.argument("provider", required=False)
@click.option("--api-key", help="API key to store for API-key providers.")
@click.option("--model", help="Reasoning model to persist with the provider selection.")
@click.option(
    "--set-provider/--no-set-provider",
    default=True,
    show_default=True,
    help="Persist LLM_PROVIDER and provider model settings after login.",
)
@click.option(
    "--validate/--no-validate",
    default=True,
    show_default=True,
    help="Run a tiny live validation request before storing API keys.",
)
@click.option(
    "--open-browser/--no-open-browser",
    default=True,
    show_default=True,
    help="Offer to open the provider setup page for API-key flows.",
)
@click.option(
    "--launch-login/--no-launch-login",
    default=True,
    show_default=True,
    help="Launch the vendor CLI login command for subscription flows when needed.",
)
def auth_login(
    provider: str | None,
    api_key: str | None,
    model: str | None,
    set_provider: bool,
    validate: bool,
    open_browser: bool,
    launch_login: bool,
) -> None:
    """Configure one LLM provider auth path."""
    profile = _resolve_or_raise(provider) if provider else _prompt_provider()
    try:
        if profile.kind == "api_key":
            _maybe_open_setup_page(profile, enabled=open_browser)
            resolved_key = api_key
            if resolved_key is None:
                resolved_key = click.prompt(
                    f"{profile.label} key",
                    hide_input=True,
                    confirmation_prompt=False,
                    type=str,
                )
            result = configure_api_key_provider(
                profile=profile,
                api_key=resolved_key,
                model=model,
                set_provider=set_provider,
                validate=validate,
            )
        else:
            _maybe_open_setup_page(profile, enabled=open_browser)
            result = configure_cli_subscription_provider(
                profile=profile,
                model=model,
                set_provider=set_provider,
                launch_login=launch_login,
            )
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Authenticated: {profile.label}")
    click.echo(f"Provider     : {result.provider}")
    click.echo(f"Credential   : {result.source}")
    if result.model:
        click.echo(f"Model        : {result.model}")
    if result.env_path:
        click.echo(f"Env          : {result.env_path}")
    click.echo(f"Status       : {result.detail}")
    click.echo(f"Verify       : opensre auth verify {result.provider}")


@auth_command.command(name="status")
@click.argument("provider", required=False)
def auth_status(provider: str | None) -> None:
    """Show provider auth status."""
    profiles = (_resolve_or_raise(provider),) if provider else iter_auth_profiles()
    for line in _status_lines(profiles):
        click.echo(line)


@auth_command.command(name="verify")
@click.argument("provider")
def auth_verify(provider: str) -> None:
    """Intentionally verify one provider's request-time credentials."""
    _resolve_or_raise(provider)
    try:
        status = verify_provider(provider)
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc
    state = "ok" if status.authenticated else "missing"
    if status.stale:
        state = "stale"
    click.echo(f"Provider : {status.provider}")
    click.echo(f"Status   : {state}")
    click.echo(f"Source   : {status.source}")
    click.echo(f"Detail   : {status.detail}")


@auth_command.command(name="logout")
@click.argument("provider")
@click.option(
    "--vendor",
    is_flag=True,
    help="Also run the vendor CLI logout command for subscription providers.",
)
def auth_logout(provider: str, vendor: bool) -> None:
    """Clear OpenSRE-managed auth for a provider."""
    _resolve_or_raise(provider)
    try:
        detail = logout_provider(provider, vendor=vendor)
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(detail)


__all__ = ["auth_command"]
