"""Lightweight coverage for deterministic action execution helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.cli.interactive_shell.action_executor import terminate_child_process


def test_terminate_child_process_noop_when_exited() -> None:
    proc = MagicMock()
    proc.poll.return_value = 0
    terminate_child_process(proc)  # should not raise
    proc.terminate.assert_not_called()
