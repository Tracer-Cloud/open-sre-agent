from __future__ import annotations

from interactive_shell.runtime.background.runner import drain_background_notices
from interactive_shell.session import ReplSession


def test_enqueue_and_drain_background_notices() -> None:
    import io

    from rich.console import Console

    session = ReplSession()
    session.enqueue_background_notice("[bold]done[/bold]")
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    drain_background_notices(session, console)

    assert session.drain_background_notices() == []
    assert "done" in console.file.getvalue()
