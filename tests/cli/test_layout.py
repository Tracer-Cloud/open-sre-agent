from __future__ import annotations

import click

from surfaces.cli.app import cli
from surfaces.cli.layout import (
    RichGroup,
    _options_from_command,
    render_help,
    render_landing,
)


def _normalized_output(output: str) -> str:
    return " ".join(output.split())


def _visible_command_names(group: click.Group) -> list[str]:
    ctx = click.Context(group)
    return [
        name
        for name in group.list_commands(ctx)
        if (command := group.get_command(ctx, name)) is not None and not command.hidden
    ]


def test_render_help_shows_all_registered_commands(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.shared.terminal.banner.banner._is_first_run", lambda: True)
    render_help(cli)
    output = _normalized_output(capsys.readouterr().out)

    assert "Usage: opensre [OPTIONS] [COMMAND] [ARGS]..." in output
    # Grouped headings; the loop below still proves no command is dropped.
    assert "Getting started:" in output
    assert "Everyday:" in output
    assert "More commands:" in output
    assert "Options:" in output
    assert "Welcome back" not in output

    for name in _visible_command_names(cli):
        assert name in output

    for label, description in _options_from_command(cli):
        assert label in output
        assert description in output


def test_render_help_includes_uninstall(capsys) -> None:
    render_help(cli)
    output = capsys.readouterr().out
    assert "uninstall" in output


def test_render_help_command_list_matches_cli_registry(capsys) -> None:
    render_help(cli)
    output = _normalized_output(capsys.readouterr().out)
    for name in _visible_command_names(cli):
        assert name in output, f"command '{name}' missing from help output"


def test_render_landing_shows_header_and_examples(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.shared.terminal.banner.banner._is_first_run", lambda: True)
    render_landing(cli)
    output = _normalized_output(capsys.readouterr().out)

    assert "OpenSRE" in output
    assert "Tips for getting started" in output
    assert (
        "open-source SRE agent for automated incident investigation and root cause analysis"
        in output
    )
    assert "Usage: opensre [OPTIONS] [COMMAND] [ARGS]..." in output
    assert "Quick start:" in output
    assert "Options:" in output

    for label, description in _options_from_command(cli):
        assert label in output
        assert description in output


def test_every_row_keeps_a_gap_between_command_and_description() -> None:
    """A label longer than the fixed column must not collide with its text.

    The landing page is the first screen a new user reads. With a hardcoded
    column narrower than the longest command, ``f"{label:<44}"`` emits the label
    unpadded and the description begins immediately after the closing quote.
    """
    from rich.console import Console

    from surfaces.cli.layout import _LANDING_EXAMPLES, _render_rows

    console = Console(force_terminal=False, width=200, record=True)

    _render_rows(console, title="Quick start", rows=_LANDING_EXAMPLES, width=42)
    output = console.export_text()

    for label, description in _LANDING_EXAMPLES:
        assert f"{label} " in output, f"{label!r} runs straight into {description!r}"


def test_rich_group_format_help_delegates_to_render_help(monkeypatch) -> None:
    called_with = []

    def fake_render_help(group: click.Group) -> None:
        called_with.append(group)

    monkeypatch.setattr("surfaces.cli.layout.render_help", fake_render_help)

    group = RichGroup(name="opensre")
    group.format_help(click.Context(group), click.HelpFormatter())

    assert len(called_with) == 1
    assert called_with[0] is group
