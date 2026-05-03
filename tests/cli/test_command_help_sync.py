"""Regression test: CLI command registration must stay in sync with help copy.

The root help view derives its command list from the live Click group at runtime.
This test ensures that every command registered in `_COMMANDS` is represented in
that derived help command list (and vice versa).
"""

from __future__ import annotations

from app.cli.__main__ import cli
from app.cli.commands import _COMMANDS
from app.cli.support.layout import _commands_from_group


def test_registered_commands_match_help_table() -> None:
    registered = {cmd.name for cmd in _COMMANDS}
    assert None not in registered, (
        "A command in _COMMANDS has no name set. "
        "Ensure every click.Command is decorated with an explicit name."
    )
    documented = {name for name, _ in _commands_from_group(cli)}

    missing_from_help = registered - documented
    missing_from_registry = documented - registered

    assert not missing_from_help, (
        f"Commands registered in _COMMANDS but missing from the rendered help list: {missing_from_help}. "
        "Ensure the root CLI group is registering all commands."
    )
    assert not missing_from_registry, (
        f"Commands shown in the rendered help list but not registered in _COMMANDS: {missing_from_registry}. "
        "Add the command to _COMMANDS in app/cli/commands/__init__.py."
    )
