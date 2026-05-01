"""Persistent command history for the interactive shell prompt."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.history import FileHistory, History, InMemoryHistory

_HISTORY_FILENAME = "interactive_history"


def prompt_history_path() -> Path:
    from app.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR / _HISTORY_FILENAME


def load_prompt_history() -> History:
    """Use persistent prompt history when possible, with an in-memory fallback."""
    try:
        path = prompt_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(path))
    except OSError:
        return InMemoryHistory()


def load_command_history_entries() -> list[str]:
    """Return persisted prompt entries in chronological order."""
    history = FileHistory(str(prompt_history_path()))
    return list(reversed(list(history.load_history_strings())))


__all__ = ["load_command_history_entries", "load_prompt_history", "prompt_history_path"]
