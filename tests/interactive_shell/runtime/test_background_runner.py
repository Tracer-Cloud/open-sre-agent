from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import Session
from surfaces.interactive_shell.runtime.background.runner import (
    drain_background_notices,
    start_background_template_investigation,
)


def test_enqueue_and_drain_background_notices() -> None:
    import io

    from rich.console import Console

    session = Session()
    session.enqueue_background_notice("[bold]done[/bold]")
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    drain_background_notices(session, console)

    assert session.drain_background_notices() == []
    assert "done" in console.file.getvalue()


def test_start_background_template_investigation_assigns_fresh_investigation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    session = Session()
    session.last_investigation_id = "inv-stale"
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    def _fake_run(
        *,
        cancel_requested,
        template_name: str,
        context_overrides,
    ) -> dict[str, object]:
        _ = (cancel_requested, template_name, context_overrides)
        return {"root_cause": "done"}

    monkeypatch.setattr(
        "surfaces.cli.investigation.run_sample_alert_for_session_background",
        _fake_run,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.background.runner.track_investigation",
        lambda **_kwargs: MagicMock(
            __enter__=MagicMock(return_value=MagicMock()),
            __exit__=MagicMock(return_value=False),
        ),
    )

    task_id = start_background_template_investigation(
        template_name="generic",
        session=session,
        console=console,
        display_command="/investigate generic",
        investigation_target="generic",
    )

    assert session.last_investigation_id
    assert session.last_investigation_id != "inv-stale"
    record = session.background_investigations[task_id]
    assert record.investigation_id == session.last_investigation_id
