"""Tests for the interactive-shell launch banner."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell import banner as banner_module


def test_banner_shows_ollama_model(monkeypatch: object) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False)

    banner_module.render_banner(console)

    output = console_file.getvalue()
    assert "ollama · qwen2.5:7b" in output
    assert "ollama · default" not in output
