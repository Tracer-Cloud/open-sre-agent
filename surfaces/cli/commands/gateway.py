"""Local HTTP gateway commands."""

from __future__ import annotations

import os

import click

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 2024


def _run_remote_gateway(
    host: str,
    port: int,
    api_key: str | None,
    investigations_dir: str | None,
    reload: bool,
    log_level: str,
) -> None:
    if api_key:
        os.environ["OPENSRE_API_KEY"] = api_key
    if investigations_dir:
        os.environ["INVESTIGATIONS_DIR"] = investigations_dir

    click.echo(f"Starting OpenSRE gateway on http://{host}:{port}")
    if reload:
        click.echo("Auto-reload enabled (development mode)")

    import uvicorn

    uvicorn.run(
        "infra.deployment.remote.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level.lower(),
    )


@click.group(name="gateway", invoke_without_command=True)
@click.option(
    "--host",
    default=_DEFAULT_HOST,
    show_default=True,
    envvar="OPENSRE_GATEWAY_HOST",
    help="Host interface to bind the local gateway to.",
)
@click.option(
    "--port",
    default=_DEFAULT_PORT,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    envvar="OPENSRE_GATEWAY_PORT",
    help="TCP port for the local gateway.",
)
@click.option(
    "--api-key",
    default=None,
    envvar="OPENSRE_API_KEY",
    help="API key for gateway endpoints.",
)
@click.option(
    "--investigations-dir",
    default=None,
    type=click.Path(path_type=str, file_okay=False, dir_okay=True),
    envvar="INVESTIGATIONS_DIR",
    help="Output directory for investigation markdown files.",
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Auto-reload on source changes (development only).",
)
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(["debug", "info", "warning", "error", "critical"], case_sensitive=False),
    help="Uvicorn log level.",
)
@click.pass_context
def gateway_command(
    ctx: click.Context,
    host: str,
    port: int,
    api_key: str | None,
    investigations_dir: str | None,
    reload: bool,
    log_level: str,
) -> None:
    """Run OpenSRE gateway servers (remote API or Telegram chat)."""
    if ctx.invoked_subcommand is None:
        _run_remote_gateway(host, port, api_key, investigations_dir, reload, log_level)


@gateway_command.command("telegram")
@click.option(
    "--poll",
    is_flag=True,
    default=False,
    help="Use long polling instead of webhook mode (local development).",
)
@click.option(
    "--host",
    default=None,
    envvar="TELEGRAM_GATEWAY_HOST",
    help="Webhook bind host (ignored in poll mode).",
)
@click.option(
    "--port",
    default=None,
    type=click.IntRange(min=1, max=65535),
    envvar="TELEGRAM_WEBHOOK_PORT",
    help="Webhook bind port (ignored in poll mode).",
)
def gateway_telegram_command(poll: bool, host: str | None, port: int | None) -> None:
    """Run the Telegram two-way messaging gateway."""
    if host:
        os.environ["TELEGRAM_GATEWAY_HOST"] = host
    if port is not None:
        os.environ["TELEGRAM_WEBHOOK_PORT"] = str(port)
    if poll:
        click.echo("Starting Telegram gateway in long-poll mode")
    else:
        click.echo("Starting Telegram gateway (webhook when TELEGRAM_WEBHOOK_URL is set)")
    from gateway.run import start_gateway

    start_gateway(poll=poll)
