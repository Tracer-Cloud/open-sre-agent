"""Tests for the /theme slash command."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.runtime import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


class TestThemeUnknownArgument:
    def test_markup_like_argument_renders_error_literally(self) -> None:
        session = Session()
        console, buf = _capture()

        assert dispatch_slash("/theme [/]", session, console, policy_precleared=True) is True

        out = buf.getvalue()
        assert "unknown theme:" in out
        assert "[/]" in out
