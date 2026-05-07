"""Slash command /model and provider switching helpers."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.rendering import render_models_table
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import PRIMARY, TERMINAL_ERROR, TEXT_DIM, WARNING


def _format_supported_models(provider_models: tuple[object, ...]) -> str:
    values = [str(getattr(model, "value", "")) for model in provider_models]
    visible = [value for value in values if value]
    return ", ".join(visible) if visible else "provider default"


def _is_model_supported(
    provider_value: str, model: str, provider_models: tuple[object, ...]
) -> bool:
    if provider_value == "ollama":
        # Ollama supports any local model name the daemon exposes.
        return bool(model)
    supported_values = {str(getattr(option, "value", "")) for option in provider_models}
    return model in supported_values


def _reset_runtime_llm_caches() -> None:
    """Force subsequent REPL assistant calls to use the updated model env."""
    from app.services.llm_client import reset_llm_singletons

    reset_llm_singletons()
    chat_module = sys.modules.get("app.nodes.chat")
    if chat_module is not None:
        chat_module.reset_chat_llm_cache()


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
            f"[{TEXT_DIM}](choices: {choices})[/]"
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
            f"[{TEXT_DIM}]set it with[/] [bold]export {provider.api_key_env}=<your-key>[/bold] "
            f"[{TEXT_DIM}]or run[/] [bold]opensre onboard[/bold] "
            f"[{TEXT_DIM}]to save it to the keyring, then rerun this command.[/]"
        )
        return False

    selected_model = model.strip() if model else provider.default_model
    if selected_model and not _is_model_supported(provider.value, selected_model, provider.models):
        console.print(
            f"[{TERMINAL_ERROR}]unknown model for {provider.value}:[/] {escape(selected_model)}"
        )
        console.print(
            "[dim]known reasoning models:[/dim] "
            f"{escape(_format_supported_models(provider.models))}"
        )
        return False

    values = {"LLM_PROVIDER": provider.value, provider.model_env: selected_model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = selected_model

    selected_toolcall: str | None = None
    if toolcall_model is not None:
        if not provider.toolcall_model_env:
            console.print(
                f"[{WARNING}]provider {provider.value} does not expose a separate "
                "toolcall model[/] — toolcall override ignored."
            )
        else:
            selected_toolcall = toolcall_model.strip()
            if selected_toolcall:
                if not _is_model_supported(provider.value, selected_toolcall, provider.models):
                    console.print(
                        f"[{TERMINAL_ERROR}]unknown model for {provider.value}:[/] "
                        f"{escape(selected_toolcall)}"
                    )
                    console.print(
                        "[dim]known toolcall models:[/dim] "
                        f"{escape(_format_supported_models(provider.models))}"
                    )
                    return False
                values[provider.toolcall_model_env] = selected_toolcall

    env_path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()

    # Be explicit about which slot each model lands in.
    console.print(f"[{PRIMARY}]switched LLM provider:[/] {provider.value}")
    console.print(
        f"[{PRIMARY}]reasoning model:[/] {selected_model or 'provider default'} "
        f"[{TEXT_DIM}]({provider.model_env})[/]"
    )
    if selected_toolcall:
        console.print(
            f"[{PRIMARY}]toolcall model:[/] {selected_toolcall} "
            f"[{TEXT_DIM}]({provider.toolcall_model_env})[/]"
        )
    console.print(f"[{TEXT_DIM}]updated {env_path}[/]")
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
            f"[{TEXT_DIM}](choices: {choices})[/]"
        )
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[{WARNING}]provider {provider.value} does not expose a separate "
            "toolcall model[/] — nothing to set."
        )
        return False
    new_model = toolcall_model.strip()
    if not new_model:
        console.print(f"[{TERMINAL_ERROR}]toolcall model cannot be empty[/]")
        return False

    values = {provider.toolcall_model_env: new_model}
    env_path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()

    console.print(
        f"[{PRIMARY}]toolcall model set to:[/] {new_model} "
        f"[{TEXT_DIM}]({provider.value} · {provider.toolcall_model_env})[/]"
    )
    console.print(f"[{TEXT_DIM}]updated {env_path}[/]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def restore_default_model(provider_name: str, console: Console) -> bool:
    """Reset a provider to its configured default reasoning model."""
    from app.cli.wizard.config import PROVIDER_BY_VALUE

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        choices = ", ".join(sorted(PROVIDER_BY_VALUE))
        console.print(
            f"[{TERMINAL_ERROR}]unknown LLM provider:[/] {escape(provider_name)} "
            f"[dim](choices: {choices})[/dim]"
        )
        return False
    return switch_llm_provider(provider.value, console, model=provider.default_model)


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


def _cmd_model(session: ReplSession, console: Console, args: list[str]) -> bool:
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
                console.print(f"[{TEXT_DIM}]usage:[/] /model toolcall set <model>")
                return True
            switch_toolcall_model(args[2], console)
            return True
        console.print(
            f"[{TEXT_DIM}]usage:[/] /model toolcall set <model> "
            f"[{TEXT_DIM}](sets the toolcall model for the active provider)[/]"
        )
        return True

    if sub in ("restore", "default", "reset"):
        if len(args) > 2:
            console.print("[dim]usage:[/dim] /model restore [provider]")
            session.mark_latest(ok=False, kind="slash")
            return True
        provider_name = args[1] if len(args) == 2 else os.getenv("LLM_PROVIDER", "anthropic")
        restored = restore_default_model(provider_name, console)
        if not restored:
            session.mark_latest(ok=False, kind="slash")
        return True

    if sub in ("set", "use", "switch"):
        try:
            provider_name, reasoning_model, tc_model = parse_model_set_args(args[1:])
        except ValueError as exc:
            console.print()
            console.print(f"[{TERMINAL_ERROR}]{escape(str(exc))}[/]")
            console.print()
            console.print(
                f"[{TEXT_DIM}]usage:[/] /model set <provider> [model] [--toolcall-model <model>]"
            )
            session.mark_latest(ok=False, kind="slash")
            return True
        switched = switch_llm_provider(
            provider_name,
            console,
            model=reasoning_model,
            toolcall_model=tc_model,
        )
        if not switched:
            session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"[{TERMINAL_ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/model show[/bold], "
        "[bold]/model set <provider> [model] [--toolcall-model <m>][/bold], "
        "[bold]/model restore [provider][/bold], "
        "or [bold]/model toolcall set <model>[/bold])"
    )
    return True


_MODEL_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("show", "show active provider and models"),
    ("set", "switch provider  ·  /model set <provider> [model]"),
    ("restore", "restore the active provider's default reasoning model"),
    ("toolcall", "manage toolcall model for the active provider"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/model",
        "show or set the active LLM ('/model show', "
        "'/model set <provider> [model] [--toolcall-model <m>]', "
        "'/model restore [provider]', "
        "'/model toolcall set <model>')",
        _cmd_model,
        first_arg_completions=_MODEL_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
]

__all__ = [
    "COMMANDS",
    "parse_model_set_args",
    "restore_default_model",
    "switch_llm_provider",
    "switch_toolcall_model",
]
