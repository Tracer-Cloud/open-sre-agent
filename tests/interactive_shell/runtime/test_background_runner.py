from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from platform.background_investigations.store import (
    BackgroundInvestigationStore,
    BackgroundInvestigationStoreLockTimeout,
)
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.runtime.background.runner import (
    drain_background_notices,
    start_background_template_investigation,
)
from surfaces.interactive_shell.session import Session

_STORE_FACTORY = "platform.background_investigations.store.background_investigation_store"


def _noop_tracker(**_kwargs: Any) -> Any:
    return MagicMock(
        __enter__=MagicMock(return_value=MagicMock()),
        __exit__=MagicMock(return_value=False),
    )


def _run_to_completion(session: Session, monkeypatch: pytest.MonkeyPatch) -> str:
    """Start a background investigation and block until its worker thread exits."""

    def _fake_run(*, cancel_requested: Any, template_name: str, context_overrides: Any) -> Any:
        _ = (cancel_requested, template_name, context_overrides)
        return {"root_cause": "pool saturation"}

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.investigation_adapter"
        ".run_sample_alert_for_session_background",
        _fake_run,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.background.runner.track_investigation",
        _noop_tracker,
    )
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    task_id = start_background_template_investigation(
        template_name="generic",
        session=session,
        console=console,
        display_command="/investigate generic",
    )
    for thread in threading.enumerate():
        if thread.name == f"background-investigation-{task_id}":
            thread.join(timeout=10)
    return task_id


def test_enqueue_and_drain_background_notices() -> None:
    import io

    from rich.console import Console

    session = Session()
    session.terminal.enqueue_background_notice("[bold]done[/bold]")
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    drain_background_notices(session, console)

    assert session.terminal.drain_background_notices() == []
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
        "surfaces.interactive_shell.runtime.investigation_adapter.run_sample_alert_for_session_background",
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
    record = session.terminal.background_investigations[task_id]
    assert record.investigation_id == session.last_investigation_id


class _RaisingStore:
    """A wedged file lock, or an org-bound home this turn may not read."""

    def __init__(self) -> None:
        self.attempts = 0

    def save(self, record: Any) -> None:
        _ = record
        self.attempts += 1
        raise BackgroundInvestigationStoreLockTimeout("locked")


def test_a_completed_investigation_is_readable_from_another_store_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Readable across processes" is only true once the record reaches the file.
    A second instance over the same path is the closest in-process proxy."""
    path = tmp_path / "background" / "investigations.json"
    monkeypatch.setattr(_STORE_FACTORY, lambda: BackgroundInvestigationStore(path))
    session = Session()

    task_id = _run_to_completion(session, monkeypatch)

    stored = BackgroundInvestigationStore(path).get(task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.root_cause == "pool saturation"


def test_a_failing_store_does_not_escape_the_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save runs in a ``finally``, so an escaping exception could not fail the
    investigation, because the ``except`` arms have already run. It would instead
    reach ``threading.excepthook`` and dump a traceback into the terminal
    prompt_toolkit is drawing. Asserting the hook stayed silent is what pins the
    guard; asserting the status alone passes with the guard deleted."""
    escaped: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", escaped.append)
    store = _RaisingStore()
    monkeypatch.setattr(_STORE_FACTORY, lambda: store)
    session = Session()

    task_id = _run_to_completion(session, monkeypatch)

    assert store.attempts == 1
    assert escaped == []
    assert session.terminal.background_investigations[task_id].status == "completed"
    assert session.terminal.drain_background_notices()


class _ExplodingAdapter:
    name = "telegram"
    capabilities = frozenset({"background_rca"})

    def deliver(self, record: Any) -> str:
        _ = record
        raise RuntimeError("transport exploded")


def test_a_raising_notification_channel_does_not_fail_the_investigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The RCA is finished by the time channels are notified, so a channel that
    blows up is a per-channel outcome, not an investigation failure. Before this
    the exception reached the worker's ``except`` arm, which rewrote a completed
    record to ``failed`` and persisted it that way."""
    from platform.notifications.outbound_registry import (
        clear_outbound_adapters,
        register_outbound_adapter,
    )

    path = tmp_path / "background" / "investigations.json"
    monkeypatch.setattr(_STORE_FACTORY, lambda: BackgroundInvestigationStore(path))
    monkeypatch.setattr("bootstrap.adapters.install_notification_adapters", lambda: ("telegram",))
    clear_outbound_adapters()
    register_outbound_adapter(_ExplodingAdapter())
    session = Session()
    session.terminal.background_notification_preferences.set_channels(["telegram"])

    task_id = _run_to_completion(session, monkeypatch)
    clear_outbound_adapters()

    stored = BackgroundInvestigationStore(path).get(task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.root_cause == "pool saturation"
    assert stored.notification_results == {"telegram": "failed: RuntimeError"}


def test_new_resets_the_session_without_deleting_durable_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4426's scoping asks that /new reset the interactive session but leave the
    durable records and the notification preferences alone."""
    path = tmp_path / "background" / "investigations.json"
    monkeypatch.setattr(_STORE_FACTORY, lambda: BackgroundInvestigationStore(path))
    session = Session()
    task_id = _run_to_completion(session, monkeypatch)
    session.terminal.background_notification_preferences.set_channels(["telegram"])

    dispatch_slash("/new", session, Console(file=io.StringIO(), force_terminal=False))

    assert BackgroundInvestigationStore(path).get(task_id) is not None
    assert session.terminal.background_investigations == {}
    assert session.terminal.background_notification_preferences.channels == ("telegram",)


def test_a_failing_store_tells_the_user_the_record_was_not_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost record is otherwise invisible: the guard keeps the traceback off the
    terminal, and the debug log the CLI never configures reaches nobody. Asserting
    a second notice, because the completion notice alone already makes the queue
    truthy."""
    monkeypatch.setattr(_STORE_FACTORY, _RaisingStore)
    session = Session()

    task_id = _run_to_completion(session, monkeypatch)

    notices = session.terminal.drain_background_notices()
    assert len(notices) == 2
    assert any("not saved" in n and task_id in n for n in notices), notices
