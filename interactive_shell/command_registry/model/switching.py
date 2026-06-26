"""Provider/model switch helpers for the /model slash command."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

import interactive_shell.command_registry.repl_data as repl_data
from interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING, render_models_table
from interactive_shell.ui.choice_menu import print_valid_choice_list


def _format_supported_models(provider_models: tuple[object, ...]) -> str:
    values = [str(getattr(model, "value", "")) for model in provider_models]
    visible = [value for value in values if value]
    return ", ".join(visible) if visible else "provider default"


def _normalize_model_id(model: str) -> str:
    """Collapse internal whitespace in a model id to single hyphens.

    A model id is a single token, so a value like ``"gpt 5.5"`` is a mis-parsed
    ``"gpt-5.5"``. The CLI path (``/model set gpt 5.5``) already rebuilds the id as
    ``gpt-5.5``; normalizing here keeps the planner/tool path
    (``llm_set_provider`` -> ``switch_reasoning_model``) consistent so a custom-model
    provider (e.g. openai) can't persist a whitespace-bearing slug that later fails
    availability checks and silently falls back.
    """
    return "-".join(model.split())


def _is_model_supported(
    _provider_value: str, model: str, provider_models: tuple[object, ...]
) -> bool:
    supported_values = {str(getattr(option, "value", "")) for option in provider_models}
    return model in supported_values


def _provider_allows_custom_models(provider: object) -> bool:
    return bool(getattr(provider, "allow_custom_models", False))


def _is_model_allowed(provider: object, model: str) -> bool:
    provider_value = str(getattr(provider, "value", ""))
    provider_models = getattr(provider, "models", ())
    if _is_model_supported(provider_value, model, provider_models):
        return True
    return bool(model) and _provider_allows_custom_models(provider)


def _reset_runtime_llm_caches() -> None:
    """Force subsequent REPL assistant calls to use the updated model env."""
    from core.runtime.llm.agent_llm_client import reset_agent_client
    from core.runtime.llm.llm_client import reset_llm_singletons

    reset_llm_singletons()
    reset_agent_client()


def switch_llm_provider(
    provider_name: str,
    console: Console,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
) -> bool:
    from cli.wizard.config import PROVIDER_BY_VALUE
    from cli.wizard.env_sync import sync_provider_env
    from config.llm_credentials import has_llm_api_key

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(provider_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
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
            f"[{ERROR}]missing credential for {provider.value}:[/] "
            f"{provider.api_key_env} is not set in env or the keyring."
        )
        console.print(
            f"[{DIM}]set it with[/] [bold]export {provider.api_key_env}=<your-key>[/bold] "
            f"[{DIM}]or run[/] [bold]opensre onboard[/bold] "
            f"[{DIM}]to save it to the keyring, then rerun this command.[/]"
        )
        return False

    selected_model = _normalize_model_id(model) if model else provider.default_model
    if selected_model and not _is_model_allowed(provider, selected_model):
        console.print(f"[{ERROR}]unknown model for {provider.value}:[/] {escape(selected_model)}")
        console.print(
            f"[{DIM}]known reasoning models:[/] {escape(_format_supported_models(provider.models))}"
        )
        return False

    selected_toolcall: str | None = None
    if toolcall_model is not None:
        if not provider.toolcall_model_env:
            console.print(
                f"[{WARNING}]provider {provider.value} does not expose a separate "
                "toolcall model[/] — toolcall override ignored."
            )
        else:
            selected_toolcall = _normalize_model_id(toolcall_model)
            if selected_toolcall and not _is_model_allowed(provider, selected_toolcall):
                console.print(
                    f"[{ERROR}]unknown model for {provider.value}:[/] {escape(selected_toolcall)}"
                )
                console.print(
                    f"[{DIM}]known toolcall models:[/] "
                    f"{escape(_format_supported_models(provider.models))}"
                )
                return False

    env_path = sync_provider_env(
        provider=provider,
        model=selected_model,
        toolcall_model=selected_toolcall or None,
    )
    _reset_runtime_llm_caches()

    # Be explicit about which slot each model lands in.
    console.print(f"[{HIGHLIGHT}]switched LLM provider:[/] {provider.value}")
    console.print(
        f"[{HIGHLIGHT}]reasoning model:[/] {selected_model or 'provider default'} "
        f"[{DIM}]({provider.model_env})[/]"
    )
    if selected_toolcall:
        console.print(
            f"[{HIGHLIGHT}]toolcall model:[/] {selected_toolcall} "
            f"[{DIM}]({provider.toolcall_model_env})[/]"
        )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def switch_toolcall_model(
    toolcall_model: str,
    console: Console,
    *,
    provider_name: str | None = None,
) -> bool:
    """Set the toolcall model for the active (or named) provider."""
    from cli.wizard.config import PROVIDER_BY_VALUE
    from cli.wizard.env_sync import sync_env_values

    raw_name = provider_name if provider_name else os.getenv("LLM_PROVIDER", "anthropic")
    resolved_name = (raw_name or "anthropic").strip().lower()
    provider = PROVIDER_BY_VALUE.get(resolved_name)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(resolved_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[{WARNING}]provider {provider.value} does not expose a separate "
            "toolcall model[/] — nothing to set."
        )
        return False
    new_model = _normalize_model_id(toolcall_model)
    if not new_model:
        console.print(f"[{ERROR}]toolcall model cannot be empty[/]")
        return False

    values = {provider.toolcall_model_env: new_model}
    env_path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()

    console.print(
        f"[{HIGHLIGHT}]toolcall model set to:[/] {new_model} "
        f"[{DIM}]({provider.value} · {provider.toolcall_model_env})[/]"
    )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def switch_reasoning_model(
    reasoning_model: str,
    console: Console,
    *,
    provider_name: str | None = None,
) -> bool:
    """Set the reasoning model for the active (or named) provider."""
    from cli.wizard.config import PROVIDER_BY_VALUE
    from cli.wizard.env_sync import sync_reasoning_model_env

    raw_name = provider_name if provider_name else os.getenv("LLM_PROVIDER", "anthropic")
    resolved_name = (raw_name or "anthropic").strip().lower()
    provider = PROVIDER_BY_VALUE.get(resolved_name)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(resolved_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False

    new_model = _normalize_model_id(reasoning_model)
    if not new_model:
        console.print(f"[{ERROR}]reasoning model cannot be empty[/]")
        return False
    if not _is_model_allowed(provider, new_model):
        console.print(f"[{ERROR}]unknown model for {provider.value}:[/] {escape(new_model)}")
        console.print(
            f"[{DIM}]known reasoning models:[/] {escape(_format_supported_models(provider.models))}"
        )
        return False

    env_path = sync_reasoning_model_env(provider=provider, model=new_model)
    _reset_runtime_llm_caches()

    console.print(
        f"[{HIGHLIGHT}]reasoning model set to:[/] {new_model} "
        f"[{DIM}]({provider.value} · {provider.model_env})[/]"
    )
    console.print(f"[{DIM}]updated {env_path}[/]")
    render_models_table(console, repl_data.load_llm_settings())
    return True


def restore_default_model(provider_name: str, console: Console) -> bool:
    """Reset a provider to its configured default reasoning model."""
    from cli.wizard.config import PROVIDER_BY_VALUE

    provider_key = provider_name.strip().lower()
    provider = PROVIDER_BY_VALUE.get(provider_key)
    if provider is None:
        console.print(f"[{ERROR}]unknown LLM provider:[/] {escape(provider_name)}")
        print_valid_choice_list(
            console,
            title="valid providers:",
            choices=sorted(PROVIDER_BY_VALUE),
        )
        return False
    return switch_llm_provider(provider.value, console, model=provider.default_model)
