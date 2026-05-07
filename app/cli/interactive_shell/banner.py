"""Identity banner rendered at REPL launch."""

from __future__ import annotations

import os

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from app.cli.interactive_shell.theme import (
    BANNER_PRIMARY,
    BANNER_SECONDARY,
    BANNER_TERTIARY,
    BANNER_UI_DIVIDER,
)
from app.config import LLMSettings
from app.version import get_version


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active reasoning/toolcall model names for a provider."""
    if provider in {"codex", "claude-code", "gemini-cli"}:
        env_key = {
            "codex": "CODEX_MODEL",
            "claude-code": "CLAUDE_CODE_MODEL",
            "gemini-cli": "GEMINI_CLI_MODEL",
        }[provider]
        cli_model = os.getenv(env_key, "").strip() or "CLI default"
        return (cli_model, cli_model)

    single_model = str(getattr(settings, f"{provider}_model", "")).strip()
    if single_model:
        return (single_model, single_model)

    reasoning_model = str(getattr(settings, f"{provider}_reasoning_model", "")).strip()
    toolcall_model = str(getattr(settings, f"{provider}_toolcall_model", "")).strip()
    return (reasoning_model or "default", toolcall_model or reasoning_model or "default")


def detect_provider_model() -> tuple[str, str]:
    """Return a human-readable (provider, model) for the active LLM config."""
    try:
        settings = LLMSettings.from_env()
    except Exception:
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv("LLM_PROVIDER", "anthropic")
    reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    return (provider, reasoning_model)


def render_banner(console: Console | None = None) -> None:
    """Print a single-line status banner and hairline rule (minimal chrome)."""
    console = console or Console(highlight=False)
    provider, model = detect_provider_model()

    line = Text()
    line.append("◆ ", style=f"bold {BANNER_PRIMARY}")
    line.append("OpenSRE", style=f"bold {BANNER_SECONDARY}")
    line.append(f"  v{get_version()}", style="dim")
    line.append("   │   ", style=BANNER_UI_DIVIDER)
    line.append(provider, style=f"bold {BANNER_SECONDARY}")
    line.append("  ·  ", style="dim")
    line.append(model, style="dim")
    line.append("   │   ", style=BANNER_UI_DIVIDER)
    line.append("Tab", style=BANNER_TERTIARY)
    line.append(" complete  ", style="dim")
    line.append("↑↓", style=BANNER_TERTIARY)
    line.append(" history", style="dim")

    console.print()
    console.print(line)
    console.print(Rule(style=BANNER_UI_DIVIDER))
    console.print()
