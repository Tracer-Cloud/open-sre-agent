from __future__ import annotations

import click

from app.cli.__main__ import cli
from app.cli.support.layout import (
    _SHORT_OPTIONS,
    RichGroup,
    render_help,
    render_landing,
)


def _normalized_output(output: str) -> str:
    return " ".join(output.split())


def test_render_help_shows_all_registered_commands(capsys) -> None:
    render_help(cli)
    output = _normalized_output(capsys.readouterr().out)

    assert "Usage: opensre [OPTIONS] COMMAND [ARGS]..." in output
    assert "Commands:" in output
    assert "Options:" in output

    ctx = click.Context(cli)
    for name in cli.list_commands(ctx):
        assert name in output

    for label, description in _SHORT_OPTIONS:
        assert label in output
        assert description in output


def test_render_help_includes_uninstall(capsys) -> None:
    render_help(cli)
    output = capsys.readouterr().out
    assert "uninstall" in output


def test_render_help_command_list_matches_cli_registry(capsys) -> None:
    render_help(cli)
    output = _normalized_output(capsys.readouterr().out)
    ctx = click.Context(cli)
    for name in cli.list_commands(ctx):
        assert name in output, f"command '{name}' missing from help output"


def test_render_landing_shows_header_and_examples(capsys) -> None:
    render_landing()
    output = _normalized_output(capsys.readouterr().out)

    assert (
        "open-source SRE agent for automated incident investigation and root cause analysis"
        in output
    )
    assert "Usage: opensre [OPTIONS] COMMAND [ARGS]..." in output
    assert "Quick start:" in output
    assert "Options:" in output

    for label, description in _SHORT_OPTIONS:
        assert label in output
        assert description in output


def test_rich_group_format_help_delegates_to_render_help(monkeypatch) -> None:
    called_with = []

    def fake_render_help(group: click.Group) -> None:
        called_with.append(group)

    monkeypatch.setattr("app.cli.support.layout.render_help", fake_render_help)

    group = RichGroup(name="opensre")
    group.format_help(click.Context(group), click.HelpFormatter())

    assert len(called_with) == 1
    assert called_with[0] is group
