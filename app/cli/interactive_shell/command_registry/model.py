"""Slash command /model and provider switching helpers."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry import repl_data
from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING, render_models_table
from app.cli.interactive_shell.ui.choice_menu import (
    CRUMB_SEP,
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)

_ROOT = "/model"  # breadcrumb root label


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
    if provider_value == "bedrock":
        # Bedrock supports any model ID, inference profile ID (us.*, eu.*, global.*),
        # or application inference profile ARN the account has access to.
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

    selected_model = model.strip() if model else provider.default_model
    if selected_model and not _is_model_supported(provider.value, selected_model, provider.models):
        console.print(f"[{ERROR}]unknown model for {provider.value}:[/] {escape(selected_model)}")
        console.print(
            f"[{DIM}]known reasoning models:[/] {escape(_format_supported_models(provider.models))}"
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
                        f"[{ERROR}]unknown model for {provider.value}:[/] "
                        f"{escape(selected_toolcall)}"
                    )
                    console.print(
                        f"[{DIM}]known toolcall models:[/] "
                        f"{escape(_format_supported_models(provider.models))}"
                    )
                    return False
                values[provider.toolcall_model_env] = selected_toolcall

    env_path = sync_env_values(values)
    os.environ.update(values)
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
    from app.cli.wizard.config import PROVIDER_BY_VALUE
    from app.cli.wizard.env_sync import sync_env_values

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
    new_model = toolcall_model.strip()
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


def restore_default_model(provider_name: str, console: Console) -> bool:
    """Reset a provider to its configured default reasoning model."""
    from app.cli.wizard.config import PROVIDER_BY_VALUE

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


def _provider_menu_choices() -> list[tuple[str, str]]:
    from app.cli.wizard.config import SUPPORTED_PROVIDERS

    current_provider = (os.getenv("LLM_PROVIDER", "anthropic") or "anthropic").strip().lower()
    options: list[tuple[str, str]] = []
    for provider in SUPPORTED_PROVIDERS:
        suffix = "*" if provider.value == current_provider else ""
        options.append((provider.value, f"{provider.value}{suffix}"))
    return options


def _reasoning_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__provider_default__", "provider default (one step)"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if getattr(provider, "value", "") == "bedrock":
        choices.append(("__custom__", "custom model / inference profile ID"))
    return choices


def _toolcall_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__keep__", "keep"),
        ("__match_reasoning__", "match-reasoning"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if getattr(provider, "value", "") == "bedrock":
        choices.append(("__custom__", "custom model / inference profile ID"))
    return choices


def _prompt_custom_model_id(console: Console) -> str | None:
    """Prompt the user to type a custom Bedrock model/inference profile ID."""
    console.print()
    console.print(
        f"[{DIM}]Enter a Bedrock model ID, inference profile ID (us.*/eu.*/global.*), "
        f"or application inference profile ARN:[/]"
    )
    try:
        value = console.input(f"[{HIGHLIGHT}]model ID> [/]").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value if value else None


def _interactive_set_provider(console: Console) -> bool | None:
    from app.cli.wizard.config import PROVIDER_BY_VALUE

    crumb_set = f"{_ROOT}{CRUMB_SEP}set"
    while True:
        provider_value = repl_choose_one(
            title="LLM provider",
            breadcrumb=crumb_set,
            choices=_provider_menu_choices(),
        )
        if provider_value is None:
            return None
        provider = PROVIDER_BY_VALUE.get(provider_value)
        if provider is None:
            return False

        crumb_model = f"{crumb_set}{CRUMB_SEP}{provider_value}"
        while True:
            reasoning_choice = repl_choose_one(
                title="reasoning model",
                breadcrumb=crumb_model,
                choices=_reasoning_model_menu_choices(provider),
            )
            if reasoning_choice is None:
                break

            if reasoning_choice == "__custom__":
                custom = _prompt_custom_model_id(console)
                if custom is None:
                    continue
                reasoning_choice = custom

            model_choice = (
                None if reasoning_choice == "__provider_default__" else str(reasoning_choice)
            )
            toolcall_model: str | None = None
            # Default reasoning: switch provider + default reasoning only — do not
            # prompt for toolcall (matches non-interactive `/model set <provider>`).
            if provider.toolcall_model_env and reasoning_choice != "__provider_default__":
                crumb_tc = f"{crumb_model}{CRUMB_SEP}toolcall"
                while True:
                    toolcall_value = repl_choose_one(
                        title="toolcall model",
                        breadcrumb=crumb_tc,
                        choices=_toolcall_model_menu_choices(provider),
                    )
                    if toolcall_value is None:
                        return None
                    if toolcall_value == "__keep__":
                        break
                    if toolcall_value == "__match_reasoning__":
                        toolcall_model = model_choice or provider.default_model
                        break
                    if toolcall_value == "__custom__":
                        custom_tc = _prompt_custom_model_id(console)
                        if custom_tc is None:
                            continue
                        toolcall_model = custom_tc
                        break
                    toolcall_model = str(toolcall_value)
                    break

            return switch_llm_provider(
                provider.value,
                console,
                model=model_choice,
                toolcall_model=toolcall_model,
            )


def _interactive_restore_provider(console: Console) -> bool | None:
    provider_value = repl_choose_one(
        title="LLM provider",
        breadcrumb=f"{_ROOT}{CRUMB_SEP}restore",
        choices=_provider_menu_choices(),
    )
    if provider_value is None:
        return None
    return restore_default_model(provider_value, console)


def _interactive_set_toolcall(console: Console) -> bool | None:
    from app.cli.wizard.config import PROVIDER_BY_VALUE

    crumb_tc = f"{_ROOT}{CRUMB_SEP}toolcall"
    provider_value = repl_choose_one(
        title="LLM provider",
        breadcrumb=crumb_tc,
        choices=_provider_menu_choices(),
    )
    if provider_value is None:
        return None
    provider = PROVIDER_BY_VALUE.get(provider_value)
    if provider is None:
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[{WARNING}]provider {provider.value} does not expose a separate "
            "toolcall model[/] — nothing to set."
        )
        return False
    model_value = repl_choose_one(
        title="toolcall model",
        breadcrumb=f"{crumb_tc}{CRUMB_SEP}{provider_value}",
        choices=_toolcall_model_menu_choices(provider),
    )
    if model_value is None:
        return None
    if model_value == "__keep__":
        console.print("[dim]toolcall model left unchanged.[/dim]")
        return True
    if model_value == "__match_reasoning__":
        reasoning = (os.getenv(provider.model_env, "") or "").strip() or provider.default_model
        return switch_toolcall_model(reasoning, console, provider_name=provider.value)
    if model_value == "__custom__":
        custom_tc = _prompt_custom_model_id(console)
        if custom_tc is None:
            return None
        model_value = custom_tc
    return switch_toolcall_model(str(model_value), console, provider_name=provider.value)


def _interactive_model_menu(session: ReplSession, console: Console) -> bool:
    while True:
        action = repl_choose_one(
            title="Select Model and Effort",
            breadcrumb=f"{_ROOT}",
            choices=[
                ("show", "show"),
                ("set", "set"),
                ("restore", "restore"),
                ("toolcall", "toolcall"),
                ("done", "done"),
            ],
        )
        if action is None or action == "done":
            return True
        if action == "show":
            repl_section_break(console)
            render_models_table(console, repl_data.load_llm_settings())
            repl_section_break(console)
            continue
        if action == "set":
            switched = _interactive_set_provider(console)
            if switched is None:
                continue
            if not switched:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
            return True
        if action == "restore":
            restored = _interactive_restore_provider(console)
            if restored is None:
                continue
            if not restored:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
            return True
        if action == "toolcall":
            switched = _interactive_set_toolcall(console)
            if switched is None:
                continue
            if not switched:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
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


def _cmd_model(session: ReplSession, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_model_menu(session, console)

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
                console.print(f"[{DIM}]usage:[/] /model toolcall set <model>")
                return True
            switch_toolcall_model(args[2], console)
            return True
        console.print(
            f"[{DIM}]usage:[/] /model toolcall set <model> "
            f"[{DIM}](sets the toolcall model for the active provider)[/]"
        )
        return True

    if sub in ("restore", "default", "reset"):
        if len(args) > 2:
            console.print(f"[{DIM}]usage:[/] /model restore [provider]")
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
            console.print(f"[{ERROR}]{escape(str(exc))}[/]")
            console.print()
            console.print(
                f"[{DIM}]usage:[/] /model set <provider> [model] [--toolcall-model <model>]"
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
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
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
        "show or set the active LLM (TTY: bare '/model' opens an inline menu — "
        "show stays open for more actions; set / restore / toolcall exit after success; "
        "done or Esc closes; TTY set: LLM provider then reasoning — pick "
        "'provider default (one step)' to skip toolcall tuning (like bare `/model set p`); "
        "else '/model show', '/model set <provider> [model] [--toolcall-model <m>]', "
        "'/model restore [provider]', '/model toolcall set <model>')",
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
