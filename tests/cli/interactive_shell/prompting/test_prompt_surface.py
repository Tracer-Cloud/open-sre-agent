"""Tests for prompt placeholder and prefill behavior."""

from __future__ import annotations

from app.cli.interactive_shell.prompting.prompt_surface import (
    _DEFAULT_PLACEHOLDER_TEXT,
    resolve_prompt_placeholder,
    wire_prompt_refresh,
)
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskKind


def _placeholder_text(session: ReplSession) -> str:
    return resolve_prompt_placeholder(session).value


class _FakeBuffer:
    def __init__(self) -> None:
        self.text = ""
        self.submitted = False

    def validate_and_handle(self) -> None:
        self.submitted = True


class _FakeApp:
    is_running = True

    def __init__(self) -> None:
        self.current_buffer = _FakeBuffer()

    def invalidate(self) -> None:
        pass


class _FakeLoop:
    def call_soon_threadsafe(self, fn, *args) -> None:  # type: ignore[no-untyped-def]
        fn(*args)


class TestPromptRefreshAutoSubmit:
    def test_queue_auto_command_fills_and_submits_prompt(self) -> None:
        """An agent-queued interactive command should be both prefilled and
        auto-submitted so it dispatches through the exclusive-stdin path."""
        session = ReplSession()
        app = _FakeApp()
        wire_prompt_refresh(session, app, _FakeLoop())
        session.queue_auto_command("/integrations setup sentry")
        assert app.current_buffer.text == "/integrations setup sentry"
        assert app.current_buffer.submitted is True

    def test_plain_prefill_does_not_auto_submit(self) -> None:
        """A prefill without the auto-submit flag must wait for the user (Enter)."""
        session = ReplSession()
        app = _FakeApp()
        wire_prompt_refresh(session, app, _FakeLoop())
        session.pending_prompt_default = "why did it fail?"
        session.notify_prompt_changed()
        assert app.current_buffer.text == "why did it fail?"
        assert app.current_buffer.submitted is False


class TestResolvePromptPlaceholder:
    def test_default_when_no_session_context(self) -> None:
        session = ReplSession()
        assert _DEFAULT_PLACEHOLDER_TEXT in _placeholder_text(session)

    def test_shows_trust_mode(self) -> None:
        session = ReplSession()
        session.trust_mode = True
        text = _placeholder_text(session)
        assert "trust on" in text
        assert _DEFAULT_PLACEHOLDER_TEXT not in text

    def test_shows_running_task_count(self) -> None:
        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        assert "1 task running" in _placeholder_text(session)

        second = session.task_registry.create(TaskKind.INVESTIGATION)
        second.mark_running()
        assert "2 tasks running" in _placeholder_text(session)

    def test_shows_resumed_session_name(self) -> None:
        session = ReplSession()
        session.resumed_from_name = "redis-incident"
        text = _placeholder_text(session)
        assert "resumed: redis-incident" in text

    def test_combines_multiple_state_segments(self) -> None:
        session = ReplSession()
        session.trust_mode = True
        session.resumed_from_name = "redis-incident"
        task = session.task_registry.create(TaskKind.WATCHDOG)
        task.mark_running()
        text = _placeholder_text(session)
        assert "trust on" in text
        assert "1 task running" in text
        assert "resumed: redis-incident" in text
        assert " · " in text
