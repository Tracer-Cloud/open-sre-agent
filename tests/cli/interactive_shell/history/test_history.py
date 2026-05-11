"""Tests for interactive shell command history helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.history import load_command_history_entries
from app.cli.interactive_shell.runtime.session import ReplSession


def _capture() -> tuple[object, object]:
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", highlight=False)
    return console, buf


def test_load_command_history_entries_returns_empty_on_mkdir_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_path = MagicMock(spec=Path)
    mock_parent = MagicMock()
    mock_path.parent = mock_parent
    mock_parent.mkdir.side_effect = OSError(13, "Permission denied")

    monkeypatch.setattr(
        "app.cli.interactive_shell.history.storage.prompt_history_path",
        lambda: mock_path,
    )

    assert load_command_history_entries() == []


def test_history_slash_command_does_not_raise_when_history_dir_unwritable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: /history must not crash the REPL when OPENSRE_HOME_DIR is not writable."""
    mock_path = MagicMock(spec=Path)
    mock_parent = MagicMock()
    mock_path.parent = mock_parent
    mock_parent.mkdir.side_effect = OSError(30, "Read-only file system")

    monkeypatch.setattr(
        "app.cli.interactive_shell.history.storage.prompt_history_path",
        lambda: mock_path,
    )

    session = ReplSession()
    console, buf = _capture()
    assert dispatch_slash("/history", session, console) is True
    assert "no history yet" in buf.getvalue()
