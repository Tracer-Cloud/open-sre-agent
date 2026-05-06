"""Slash command /model and provider switching helpers."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.rendering import render_models_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ERROR


def switch_llm_provider(
    provider_name: str,
    console: Console,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
) -> bool:
    from app.cli.wizard.config import PROVIDER_BY_VALUE
    from app.cli.wizard.env_sync import sync_env_values
    from app.llm_credentials import has_llm_api_key

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        choices = ", ".join(sorted(PROVIDER_BY_VALUE))
        console.print(
            f"[{TERMINAL_ERROR}]unknown LLM provider:[/] {escape(provider_name)} "
            f"[dim](choices: {choices})[/dim]"
        )
        return False

    # Refuse to half-update .env when the target provider has no usable
    # credential. Without this the user lands in a state where LLM_PROVIDER
    # points at e.g. anthropic but ANTHROPIC_API_KEY is unset, so the very
    # next call into LLMSettings.from_env() raises and /model show prints
    # "LLM settings unavailable" — which is exactly what reviewers caught
    # in #1192. Skip the check for providers whose credential isn't a
    # secret (ollama uses OLLAMA_HOST which has a working default) and for
    # CLI-backed providers (codex, claude-code) that authenticate through
    # the vendor CLI and have no api_key_env at all.
    if (
        provider.credential_secret
        and provider.api_key_env
        and not has_llm_api_key(provider.api_key_env)
    ):
        console.print(
            f"[{TERMINAL_ERROR}]missing credential for {provider.value}:[/] "
            f"{provider.api_key_env} is not set in env or the keyring."
        )
        console.print(
            f"[dim]set it with[/dim] [bold]export {provider.api_key_env}=<your-key>[/bold] "
            "[dim]or run[/dim] [bold]opensre onboard[/bold] "
            "[dim]to save it to the keyring, then rerun this command.[/dim]"
        )
        return False

    selected_model = model.strip() if model else provider.default_model
    values = {"LLM_PROVIDER": provider.value, provider.model_env: selected_model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = selected_model

    selected_toolcall: str | None = None
    if toolcall_model is not None:
        if not provider.toolcall_model_env:
            console.print(
                f"[yellow]provider {provider.value} does not expose a separate "
                "toolcall model[/yellow] — toolcall override ignored."
            )
        else:
            selected_toolcall = toolcall_model.strip()
            if selected_toolcall:
                values[provider.toolcall_model_env] = selected_toolcall

    env_path = sync_env_values(values)
    os.environ.update(values)

    # Be explicit about which slot each model lands in.
    console.print(f"[green]switched LLM provider:[/green] {provider.value}")
    console.print(
        f"[green]reasoning model:[/green] {selected_model or 'provider default'} "
        f"[dim]({provider.model_env})[/dim]"
    )
    if selected_toolcall:
        console.print(
            f"[green]toolcall model:[/green] {selected_toolcall} "
            f"[dim]({provider.toolcall_model_env})[/dim]"
        )
    console.print(f"[dim]updated {env_path}[/dim]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def switch_toolcall_model(
    toolcall_model: str,
    console: Console,
    *,
    provider_name: str | None = None,
) -> bool:
    """Set the toolcall model for the active (or named) provider."""
    from app.cli.wizard.config import PROVIDER_BY_VALUE
    from app.cli.wizard.env_sync import sync_env_values

    raw_name = provider_name if provider_name else os.getenv("LLM_PROVIDER", "anthropic")
    resolved_name = (raw_name or "anthropic").strip().lower()
    provider = PROVIDER_BY_VALUE.get(resolved_name)
    if provider is None:
        choices = ", ".join(sorted(PROVIDER_BY_VALUE))
        console.print(
            f"[{TERMINAL_ERROR}]unknown LLM provider:[/] {escape(resolved_name)} "
            f"[dim](choices: {choices})[/dim]"
        )
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[yellow]provider {provider.value} does not expose a separate "
            "toolcall model[/yellow] — nothing to set."
        )
        return False
    new_model = toolcall_model.strip()
    if not new_model:
        console.print(f"[{TERMINAL_ERROR}]toolcall model cannot be empty[/]")
        return False

    values = {provider.toolcall_model_env: new_model}
    env_path = sync_env_values(values)
    os.environ.update(values)

    console.print(
        f"[green]toolcall model set to:[/green] {new_model} "
        f"[dim]({provider.value} · {provider.toolcall_model_env})[/dim]"
    )
    console.print(f"[dim]updated {env_path}[/dim]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def parse_model_set_args(args: list[str]) -> tuple[str, str | None, str | None]:
    """Parse `set <provider> [reasoning_model] [--toolcall-model <m>]`.

    ``args`` is the slice after the ``set``/``use``/``switch`` keyword.

    Raises :class:`ValueError` with a user-facing message when the input is
    malformed.
    """
    if not args:
        raise ValueError("missing provider name")

    provider = args[0]
    reasoning_model: str | None = None
    toolcall_model: str | None = None

    i = 1
    while i < len(args):
        token = args[i]
        if token == "--toolcall-model":
            if i + 1 >= len(args):
                raise ValueError("missing value for --toolcall-model")
            toolcall_model = args[i + 1]
            i += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unknown flag: {token}")
        if reasoning_model is not None:
            raise ValueError(f"unexpected extra argument: {token}")
        reasoning_model = token
        i += 1

    return provider, reasoning_model, toolcall_model


def _cmd_model(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "show").strip()

    if sub == "show":
        render_models_table(console, repl_data.load_llm_settings())
        return True

    if sub == "toolcall":
        if len(args) >= 2 and args[1].lower() == "show":
            render_models_table(console, repl_data.load_llm_settings())
            return True
        if len(args) >= 2 and args[1].lower() in ("set", "use", "switch"):
            if len(args) < 3:
                console.print("[dim]usage:[/dim] /model toolcall set <model>")
                return True
            switch_toolcall_model(args[2], console)
            return True
        console.print(
            "[dim]usage:[/dim] /model toolcall set <model> "
            "[dim](sets the toolcall model for the active provider)[/dim]"
        )
        return True

    if sub in ("set", "use", "switch"):
        try:
            provider_name, reasoning_model, tc_model = parse_model_set_args(args[1:])
        except ValueError as exc:
            console.print()
            console.print(f"[{TERMINAL_ERROR}]{escape(str(exc))}[/]")
            console.print()
            console.print(
                "[dim]usage:[/dim] /model set <provider> [model] [--toolcall-model <model>]"
            )
            return True
        switch_llm_provider(
            provider_name,
            console,
            model=reasoning_model,
            toolcall_model=tc_model,
        )
        return True

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/model show[/bold], "
        "[bold]/model set <provider> [model] [--toolcall-model <m>][/bold], "
        "or [bold]/model toolcall set <model>[/bold])"
    )
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/model",
        "show or set the active LLM ('/model show', "
        "'/model set <provider> [model] [--toolcall-model <m>]', "
        "'/model toolcall set <model>')",
        _cmd_model,
    ),
]

__all__ = ["COMMANDS", "parse_model_set_args", "switch_llm_provider", "switch_toolcall_model"]
