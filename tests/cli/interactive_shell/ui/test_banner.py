"""Tests for the interactive-shell launch banner."""

from __future__ import annotations

import io

from rich.console import Console

from cli.interactive_shell.ui import banner as banner_module
from cli.interactive_shell.ui import banner_state as banner_state_module


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


def test_get_username_prefers_github_handle(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "_github_username", lambda: "octocat")
    monkeypatch.setattr(banner_module.getpass, "getuser", lambda: "system-user")

    assert banner_module._get_username() == "octocat"


def test_get_username_falls_back_to_system_user(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "_github_username", lambda: "")
    monkeypatch.setattr(banner_module.getpass, "getuser", lambda: "system-user")

    assert banner_module._get_username() == "system-user"


def test_github_username_reads_saved_credential(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "integrations.store.get_integration",
        lambda service: {"credentials": {"username": "octocat"}} if service == "github" else None,
    )

    assert banner_module._github_username() == "octocat"


def test_github_username_empty_when_not_configured(monkeypatch: object) -> None:
    monkeypatch.setattr("integrations.store.get_integration", lambda _service: None)

    assert banner_module._github_username() == ""


def test_ambient_column_marks_incomplete_integration(monkeypatch: object) -> None:
    # A hosted MCP record saved without an API token is "present" but cannot
    # connect; the banner must mark it rather than imply it works.
    monkeypatch.setattr(
        banner_state_module,
        "_load_integration_health",
        lambda: [("Sentry", "ok"), ("Posthog_Mcp", "incomplete")],
    )
    monkeypatch.setattr(banner_state_module, "_is_alert_listener_active", lambda: False)

    text = banner_state_module._build_ambient_right_column().plain

    assert "Sentry" in text
    assert "Posthog_Mcp ⚠" in text
    assert "⚠ incomplete — run /integrations verify" in text


def test_ambient_column_no_warning_when_all_healthy(monkeypatch: object) -> None:
    monkeypatch.setattr(
        banner_state_module,
        "_load_integration_health",
        lambda: [("Sentry", "ok"), ("GitHub", "ok")],
    )
    monkeypatch.setattr(banner_state_module, "_is_alert_listener_active", lambda: False)

    text = banner_state_module._build_ambient_right_column().plain

    assert "Sentry" in text
    assert "GitHub" in text
    assert "⚠" not in text


def test_ready_box_expands_to_console_width() -> None:
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    banner_module.render_ready_box(console)

    lines = [
        line for line in console_file.getvalue().splitlines() if line.startswith(("╭", "╰", "│"))
    ]
    assert lines
    assert max(len(line) for line in lines) == 120
