"""Programmatic parity validation between the Click CLI and the REPL slash commands."""

from app.cli.__main__ import cli
from app.cli.interactive_shell.command_registry import SLASH_COMMANDS

# Commands that are intentionally excluded from the REPL (e.g. they don't make sense in session).
# 'agent' is excluded because the REPL itself is the agent entry point.
EXCLUDED_COMMANDS = {"agent"}


def test_cli_slash_command_parity():
    """Ensure every top-level Click command has a corresponding slash command in the REPL."""
    # Get all registered top-level commands from the main Click group
    cli_commands = set(cli.commands.keys())

    # Filter out excluded commands
    expected_commands = cli_commands - EXCLUDED_COMMANDS

    # Get all registered slash commands (strip leading slash for comparison)
    registered_slash_names = {name.lstrip("/") for name in SLASH_COMMANDS}

    # Find missing commands
    missing = expected_commands - registered_slash_names

    assert not missing, (
        f"The following CLI commands are missing from the REPL slash-command registry: {missing}"
    )


def test_slash_command_help_parity():
    """Ensure slash command help strings are descriptive."""
    for name, cmd in SLASH_COMMANDS.items():
        assert len(cmd.help_text) > 10, f"Help text for {name} is too short or missing."
        if name in {"/integrations", "/remote", "/tests", "/guardrails"}:
            assert "(" in cmd.help_text, (
                f"Help text for {name} should list common subcommands in parentheses."
            )
