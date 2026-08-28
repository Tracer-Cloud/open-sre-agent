"""Tests for the interactive-shell launch banner."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.ui import poster as poster_module
from surfaces.shared.terminal.banner import banner as banner_module
from surfaces.shared.terminal.banner import banner_state as banner_state_module


def test_integration_display_name_preserves_brand_casing() -> None:
    assert banner_state_module.integration_display_name("servicenow") == "ServiceNow"
    assert banner_state_module.integration_display_name("jira") == "Jira"


def test_banner_shows_ollama_model(monkeypatch: object) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False)

    banner_module.render_ready_box(console)

    output = console_file.getvalue()
    assert "ollama" in output
    assert "qwen2.5:7b" in output
    assert "ollama · default" not in output


def test_ready_box_uses_active_theme_palette() -> None:
    from infrastructure.terminal.theme import set_active_theme

    set_active_theme("pink")
    pink_rgb = "255;179;217"
    green_rgb = "185;237;175"

    console = Console(record=True, width=120)
    console.print(banner_module.build_ready_panel(console))

    styled = console.export_text(styles=True)
    assert pink_rgb in styled
    assert green_rgb not in styled


def test_refresh_welcome_poster_uses_repl_safe_render(monkeypatch: object) -> None:
    console = Console(record=True, width=120)
    render_calls: list[dict[str, object | None]] = []

    monkeypatch.setattr(
        "surfaces.shared.terminal.components.rendering.repl_clear_screen",
        lambda: None,
    )

    def _fake_render(
        _console: Console,
        *,
        session: object = None,
        theme_notice: str | None = None,
    ) -> None:
        render_calls.append({"session": session, "theme_notice": theme_notice})

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.poster.repl_render_launch_poster",
        _fake_render,
    )

    poster_module.refresh_welcome_poster(console, session="sess", theme_notice="pink")

    assert render_calls == [{"session": "sess", "theme_notice": "pink"}]


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


def test_github_username_survives_identity_import_failure(monkeypatch: object) -> None:
    import builtins

    real_import = builtins.__import__

    def _fail_github_identity(name: str, *args: object, **kwargs: object) -> object:
        if name == "integrations.github.identity":
            raise ImportError("simulated heavy import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_github_identity)

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


def test_ambient_column_shows_skill_count(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_state_module, "_load_integration_health", lambda: [])
    monkeypatch.setattr(banner_state_module, "_is_alert_listener_active", lambda: False)
    monkeypatch.setattr(banner_state_module, "_count_loaded_skills", lambda: 7)
    monkeypatch.setattr(banner_state_module, "_count_scheduled_tasks", lambda: 3)

    text = banner_state_module._build_ambient_right_column().plain

    assert "Skills (7) loaded into cyberdeck" in text
    assert "Scheduled tasks (3)" in text


def test_count_loaded_skills_survives_loader_failure(monkeypatch: object) -> None:
    import builtins

    real_import = builtins.__import__

    def _fail_skills_loader(name: str, *args: object, **kwargs: object) -> object:
        # The banner reaches the loader through the harness SPI; fail the import
        # whichever API module it comes through so the guard is about the loader, not the path.
        if name in ("core.agent_harness.prompts.skills.loader", "core.agent_harness.spi.grounding"):
            raise ImportError("simulated heavy import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_skills_loader)

    assert banner_state_module._count_loaded_skills() == 0


def test_count_scheduled_tasks_survives_store_failure(monkeypatch: object) -> None:
    import builtins

    real_import = builtins.__import__

    def _fail_scheduler_store(name: str, *args: object, **kwargs: object) -> object:
        if name == "infrastructure.scheduling.scheduler.store":
            raise ImportError("simulated scheduler store failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_scheduler_store)

    assert banner_state_module._count_scheduled_tasks() == 0


def test_ready_box_expands_to_console_width() -> None:
    from rich.cells import cell_len

    for width in (120, 80, 50):
        console = Console(record=True, force_terminal=False, highlight=False, width=width)
        banner_module.render_ready_box(console)
        plain = console.export_text(styles=False)
        box_lines = [line for line in plain.splitlines() if line.startswith(("╭", "│", "╰"))]
        assert box_lines
        assert {cell_len(line.rstrip()) for line in box_lines} == {
            max(cell_len(line.rstrip()) for line in box_lines)
        }
        assert all(cell_len(line.rstrip()) <= width for line in plain.splitlines())


def test_identity_greeting_matches_first_run_state(monkeypatch: object) -> None:
    """First run greets 'Welcome'; a returning install greets 'Welcome back'."""
    # Arrange: fix the username so the assertion is exact.
    monkeypatch.setattr(banner_module, "_get_username", lambda: "casey")

    # Act
    first = banner_module._build_identity_block(
        "openai", "gpt-5.4-mini", trust_mode=False, first_run=True
    ).plain
    returning = banner_module._build_identity_block(
        "openai", "gpt-5.4-mini", trust_mode=False, first_run=False
    ).plain

    # Assert
    assert "Welcome casey!" in first
    assert "Welcome back" not in first
    assert "Welcome back casey!" in returning


def test_ready_box_greets_first_run_without_welcome_back(monkeypatch: object) -> None:
    # Arrange: a machine where the wizard has never completed.
    monkeypatch.setattr(banner_module, "_is_first_run", lambda: True)
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    # Act
    banner_module.render_ready_box(console)

    # Assert
    output = console_file.getvalue()
    assert "Welcome" in output
    assert "Welcome back" not in output
