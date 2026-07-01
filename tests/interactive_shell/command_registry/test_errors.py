from unittest.mock import MagicMock

from rich.console import Console

from surfaces.interactive_shell.command_registry.errors import (
    no_output_guard,
    unknown_subcommand_handler,
)
from surfaces.interactive_shell.runtime import ReplSession


def test_unknown_subcommand_handler(capsys):
    console = Console(force_terminal=False, force_interactive=False, width=80)
    session = ReplSession("test-session")
    session.mark_latest = MagicMock()

    handler = unknown_subcommand_handler("/testcmd", (("sub1", "does thing 1"),))
    result = handler(session, console, "badsub")

    assert result is True
    session.mark_latest.assert_called_once_with(ok=False, kind="slash")
    captured = capsys.readouterr()
    assert "Unknown subcommand" in captured.out
    assert "badsub" in captured.out
    assert "Usage:" in captured.out
    assert "/testcmd sub1" in captured.out
    assert "does thing 1" in captured.out


def test_no_output_guard_produces_fallback(capsys):
    console = Console(force_terminal=False, force_interactive=False, width=80)
    session = ReplSession("test-session")

    @no_output_guard("/testguard", "Try something else.")
    def my_cmd(session: ReplSession, console: Console, args: list[str]) -> bool:
        # produces no output
        return True

    result = my_cmd(session, console, [])
    assert result is True
    captured = capsys.readouterr()
    assert "No output produced for /testguard" in captured.out
    assert "Try something else." in captured.out


def test_no_output_guard_ignores_when_output_exists(capsys):
    console = Console(force_terminal=False, force_interactive=False, width=80)
    session = ReplSession("test-session")

    @no_output_guard("/testguard", "Try something else.")
    def my_cmd(session: ReplSession, console: Console, args: list[str]) -> bool:
        console.print("I have output!")
        return True

    result = my_cmd(session, console, [])
    assert result is True
    captured = capsys.readouterr()
    assert "I have output!" in captured.out
    assert "No output produced for" not in captured.out
