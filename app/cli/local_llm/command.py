"""Zero-config local LLM setup command: opensre onboard local_llm."""

from __future__ import annotations

import questionary
from rich.console import Console

from app.cli.interactive_shell.ui.theme import DIM, ERROR, HIGHLIGHT, WARNING
from app.cli.local_llm.hardware import detect_hardware, recommend_model
from app.cli.local_llm.ollama import (
    install,
    is_installed,
    is_server_running,
    normalize_model_tag,
    pull_model,
    start_server,
    wait_for_server,
)
from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.store import get_store_path, save_local_config
from app.cli.wizard.validation import _check_ollama
from app.config import DEFAULT_OLLAMA_HOST

_console = Console()


def run_local_llm_setup() -> int:
    _console.rule("[bold]OpenSRE · Local LLM Setup[/bold]")
    _console.print(f"[{DIM}]No API key required — runs entirely on your machine.[/]\n")

    with _console.status("Detecting hardware...", spinner="dots"):
        hw = detect_hardware()
    arch_label = "Apple Silicon" if hw.is_apple_silicon else hw.arch
    _console.print(f"Hardware: [bold]{hw.total_ram_gb:.0f}GB RAM[/bold] · {arch_label}")

    if not is_installed():
        _console.print(f"\n[{WARNING}]Ollama is not installed.[/]")
        if not install(_console):
            _console.print(f"[{ERROR}]Ollama installation failed or was skipped.[/]")
            _console.print(
                "Install manually from https://ollama.com and rerun: [bold]opensre onboard local_llm[/bold]"
            )
            return 1
        if not is_installed():
            _console.print(f"[{ERROR}]Ollama still not found after install. Check your PATH.[/]")
            return 1
        _console.print(f"[{HIGHLIGHT}]Ollama installed.[/]")

    host = DEFAULT_OLLAMA_HOST
    if not is_server_running(host):
        _console.print("\nStarting Ollama server...")
        server_proc = start_server()
        with _console.status("Waiting for Ollama to be ready...", spinner="dots"):
            if not wait_for_server(host):
                server_proc.terminate()
                _console.print(f"[{ERROR}]Ollama server did not start within 30s at {host}.[/]")
                _console.print(
                    "Try running [bold]ollama serve[/bold] in a separate terminal, then rerun."
                )
                return 1
    _console.print(f"[{HIGHLIGHT}]Ollama server running[/] at {host}")

    model, reason = recommend_model(hw)
    _console.print(f"\nRecommended model: [bold]{model}[/bold]")
    _console.print(f"[{DIM}]{reason}[/]")
    chosen = questionary.text(
        "Model to use (press Enter to accept recommendation):",
        default=model,
    ).ask()
    if not chosen:
        return 1
    chosen = normalize_model_tag(chosen.strip())  # Ensure explicit tag

    _console.print()
    if not pull_model(chosen, _console, host=host):
        _console.print(f"[{ERROR}]Failed to pull model '{chosen}'.[/]")
        _console.print("Check the model name and try: [bold]ollama pull " + chosen + "[/bold]")
        return 1

    result = _check_ollama(host=host, model=chosen)
    if not result.ok:
        _console.print(f"[{ERROR}]{result.detail}[/]")
        return 1

    provider = PROVIDER_BY_VALUE["ollama"]
    env_path = sync_provider_env(provider=provider, model=chosen)
    sync_env_values({provider.api_key_env: host})
    store_path = get_store_path()
    save_local_config(
        wizard_mode="quickstart",
        provider=provider.value,
        model=chosen,
        api_key_env=provider.api_key_env,
        model_env=provider.model_env,
        probes={},
        path=store_path,
    )

    # 8. Summary
    _console.print()
    _console.rule(f"[{HIGHLIGHT}]Setup complete[/]")
    _console.print("Provider:  [bold]Ollama (local)[/bold]")
    _console.print(f"Model:     [bold]{chosen}[/bold]")
    _console.print(f"Config:    [{DIM}]{env_path}[/]")
    _console.print(f"Store:     [{DIM}]{store_path}[/]")
    _console.print("\nTry it now:")
    _console.print(
        "  [bold]opensre investigate[/bold]   — launches interactive mode, try a sample alert"
    )
    _console.print("  [bold]opensre onboard[/bold]       — configure observability integrations")
    return 0
