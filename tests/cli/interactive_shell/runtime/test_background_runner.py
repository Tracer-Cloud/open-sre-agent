from __future__ import annotations

from app.cli.interactive_shell.runtime.background_runner import drain_background_notices
from app.cli.interactive_shell.runtime.session import ReplSession


def test_enqueue_and_drain_background_notices() -> None:
    import io

    from rich.console import Console

    session = ReplSession()
    session.enqueue_background_notice("[bold]done[/bold]")
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    drain_background_notices(session, console)

    assert session.drain_background_notices() == []
    assert "done" in console.file.getvalue()
