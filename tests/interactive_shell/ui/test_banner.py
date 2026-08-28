"""Tests for the compact interactive-shell launch banner."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from config.version import get_opensre_version
from surfaces.interactive_shell.ui import poster as poster_module
from surfaces.shared.terminal.banner import banner as banner_module
from surfaces.shared.terminal.banner import banner_state as banner_state_module
from surfaces.shared.terminal.banner.banner_state import LaunchStatus


def _fixed_status() -> LaunchStatus:
    return LaunchStatus(
        skill_count=21,
        mcp_count=2,
        mcps_ready=True,
        agents_md_available=True,
    )


def test_launch_banner_is_borderless_and_shows_only_compact_identity(
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    monkeypatch.setattr(banner_module, "get_opensre_version", lambda: "2026.8.27+main.85fd865")
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    banner_module.render_launch_banner(console)

    output = console_file.getvalue()
    assert "opensre  ·  v2026.8.27+main.85fd865" in output
    assert "Skills (21) ✓" in output
    assert "MCPs (2) ✓" in output
    assert "AGENTS.md ✓" in output
    assert "Welcome" not in output
    assert "Integrations" not in output
    assert not any(char in output for char in "╭╮╰╯│")


def test_launch_banner_draws_two_overlapping_equal_rings(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    assert "    ••••••••••    " in output
    assert "•• ••        •• ••" in output
    assert " ••••••    •••••• " in output


def test_launch_banner_uses_active_theme_palette(monkeypatch: object) -> None:
    from infrastructure.terminal.theme import set_active_theme

    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    set_active_theme("pink")
    pink_rgb = "255;179;217"
    green_rgb = "185;237;175"

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
            mcp_count=0,
            mcps_ready=False,
            agents_md_available=False,
        ),
    )
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    assert "Skills (0) ✗" in output
    assert "MCPs (0) ✗" in output
    assert "AGENTS.md ✗" in output


def test_mcp_health_counts_only_mcp_integrations(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "integrations.catalog.configured_integration_health",
        lambda: [("datadog", "ok"), ("github", "ok"), ("openclaw", "incomplete")],
    )

    assert banner_state_module._mcp_health() == (2, False)


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
    assert banner_state_module._mcp_health() == (0, False)


def test_agents_md_status_tracks_repository_instructions(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(banner_state_module, "REPO_ROOT", tmp_path)
    assert banner_state_module._has_agents_md() is False

    (tmp_path / "AGENTS.md").write_text("# instructions\n", encoding="utf-8")
    assert banner_state_module._has_agents_md() is True


def test_banner_uses_runtime_version(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    details = banner_module._build_details(_fixed_status()).plain
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
