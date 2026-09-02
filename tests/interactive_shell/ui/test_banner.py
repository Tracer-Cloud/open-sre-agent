"""Tests for the interactive-shell launch banner."""

from __future__ import annotations

import io

from rich.console import Console

from config.version import get_opensre_version
from surfaces.interactive_shell.ui import poster as poster_module
from surfaces.shared.terminal.banner import banner as banner_module
from surfaces.shared.terminal.banner import banner_state as banner_state_module
from surfaces.shared.terminal.banner.banner_state import LaunchStatus


def _rgb(hex_color: str) -> str:
    """``"#RRGGBB"`` → the ``"r;g;b"`` truecolor triple."""
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"


def _fixed_status() -> LaunchStatus:
    return LaunchStatus(
        skill_count=21,
        integration_count=2,
    )


def test_launch_banner_is_borderless_centered_hero(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    monkeypatch.setattr(banner_module, "get_opensre_version", lambda: "0.1.2026.9.2+main.baba83a")
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    banner_module.render_launch_banner(console)

    output = console_file.getvalue()
    assert "opensre" in output
    assert "v0.1" in output
    assert "Skills (21) ✓" in output
    assert "Integrations (2) ✓" in output
    assert "TIP" in output
    assert "/theme" in output
    assert "Ctrl+O" in output
    assert "/ commands" in output
    # Only the two capability items — no MCPs or AGENTS.md line.
    assert "MCPs" not in output
    assert "AGENTS.md" not in output
    assert "Welcome" not in output  # welcome copy lives on the sign-in screen, not the banner
    assert not any(char in output for char in "╭╮╰╯│")


def test_launch_banner_draws_two_overlapping_equal_rings(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    # Braille rendering of the canonical OpenSRE "O" mark: the two ring-wall rows.
    assert "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿" in output


def test_launch_banner_uses_active_theme_palette(monkeypatch: object) -> None:
    from infrastructure.terminal.theme import THEME_REGISTRY, set_active_theme

    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    set_active_theme("pink")
    pink_rgb = _rgb(THEME_REGISTRY["pink"].HIGHLIGHT)
    green_rgb = _rgb(THEME_REGISTRY["green"].HIGHLIGHT)

    console = Console(record=True, width=120)
    console.print(banner_module.build_launch_banner(console))

    styled = console.export_text(styles=True)
    assert pink_rgb in styled
    assert green_rgb not in styled


def test_launch_banner_stacks_without_overflow_on_narrow_terminals(
    monkeypatch: object,
) -> None:
    from rich.cells import cell_len

    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    for width in (80, 50, 36):
        console = Console(record=True, force_terminal=False, highlight=False, width=width)
        banner_module.render_launch_banner(console)
        plain = console.export_text(styles=False)
        assert all(cell_len(line.rstrip()) <= width for line in plain.splitlines())


def test_status_marks_empty_or_unavailable_capabilities(monkeypatch: object) -> None:
    monkeypatch.setattr(
        banner_module,
        "load_launch_status",
        lambda: LaunchStatus(
            skill_count=0,
            integration_count=0,
        ),
    )
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    assert "Skills (0) ✗" in output
    assert "Integrations (0) ✗" in output


def test_integration_count_includes_all_configured(monkeypatch: object) -> None:
    # Every configured integration counts (not just MCP ones), any health state.
    monkeypatch.setattr(
        "integrations.catalog.configured_integration_health",
        lambda: [("datadog", "ok"), ("github", "ok"), ("openclaw", "incomplete")],
    )

    assert banner_state_module._count_configured_integrations() == 3


def test_status_probes_survive_loader_failures(monkeypatch: object) -> None:
    import builtins

    real_import = builtins.__import__

    def _fail_startup_probes(name: str, *args: object, **kwargs: object) -> object:
        if name in {
            "core.agent_harness.spi.grounding",
            "integrations.catalog",
        }:
            raise ImportError("simulated startup probe failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_startup_probes)

    assert banner_state_module._count_loaded_skills() == 0
    assert banner_state_module._count_configured_integrations() == 0


def test_banner_shows_the_full_build_version(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    details = banner_module._build_details(_fixed_status()).plain
    # The banner shows the full build version (identity for support / bug reports).
    assert f"v{get_opensre_version()}" in details


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
