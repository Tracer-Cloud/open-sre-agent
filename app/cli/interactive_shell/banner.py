"""Identity banner rendered at REPL launch."""

from __future__ import annotations

import os

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from app.cli.interactive_shell.theme import (
    BANNER_BORDER,
    BANNER_PRIMARY,
    BANNER_SECONDARY,
    BANNER_TERTIARY,
)
from app.config import LLMSettings
from app.version import get_version


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active reasoning/toolcall model names for a provider."""
    if provider == "codex":
        codex_model = os.getenv("CODEX_MODEL", "").strip() or "CLI default"
        return (codex_model, codex_model)

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
    except Exception:  # noqa: BLE001
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv("LLM_PROVIDER", "anthropic")
    reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    return (provider, reasoning_model)


def render_banner(console: Console | None = None) -> None:
    """Print the REPL identity banner.

    The panel expands to the full terminal width, leaving only Rich's
    default 1-char margin on each side. Content inside is padded and
    centered for a clean Claude-Code-style welcome.
    """
    console = console or Console(highlight=False)
    provider, model = detect_provider_model()

    title = Text()
    title.append("◉  ", style=f"bold {BANNER_PRIMARY}")
    title.append("OpenSRE", style=f"bold {BANNER_SECONDARY}")
    title.append("  ·  ", style="dim")
    title.append(f"v{get_version()}", style=BANNER_TERTIARY)

    info = Text()
    info.append("model  ", style="dim")
    info.append(f"{provider} · {model}", style=BANNER_SECONDARY)
    info.append("\n")
    info.append("mode   ", style="dim")
    info.append("interactive · read-only tools", style="")

    hints = Text()
    hints.append("/help", style=f"bold {BANNER_SECONDARY}")
    hints.append(" for commands", style="dim")
    hints.append("   ·   ", style="dim")
    hints.append("/status", style=f"bold {BANNER_PRIMARY}")
    hints.append(" for setup", style="dim")
    hints.append("   ·   ", style="dim")
    hints.append("/exit", style=f"bold {BANNER_SECONDARY}")
    hints.append(" to quit", style="dim")

    body = Group(
        Align.center(title),
        Text(""),
        Align.center(info),
        Text(""),
        Align.center(hints),
    )

    console.print()
    console.print(
        Panel(
            body,
            border_style=BANNER_BORDER,
            padding=(1, 2),
            expand=True,
        )
    )
    console.print()
