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
    assert "ollama" in output
    assert "qwen2.5:7b" in output
    assert "ollama · default" not in output


def test_ready_box_expands_to_console_width() -> None:
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    banner_module.render_ready_box(console)

    lines = [
        line for line in console_file.getvalue().splitlines() if line.startswith(("╭", "╰", "│"))
    ]
    assert lines
    assert max(len(line) for line in lines) == 120
