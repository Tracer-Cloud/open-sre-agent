"""Tests for foreground OpenSRE CLI subprocess execution."""

from __future__ import annotations

import subprocess
from typing import NoReturn

import pytest

from tools.interactive_shell.cli import run_foreground_cli


def test_run_foreground_cli_decodes_timeout_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(
            cmd=["opensre", "health"],
            timeout=1,
            output=b"partial stdout\n",
            stderr=b"partial stderr\n",
        )

    monkeypatch.setattr("tools.interactive_shell.cli.subprocess.run", _raise)

    result = run_foreground_cli(["opensre", "health"], timeout_seconds=1)

    assert result.timed_out is True
    assert result.stdout == "partial stdout\n"
    assert result.stderr == "partial stderr\n"
