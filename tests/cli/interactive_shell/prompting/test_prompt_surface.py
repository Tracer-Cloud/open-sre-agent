"""Tests for prompt placeholder and prefill behavior."""

from __future__ import annotations

from app.cli.interactive_shell.prompting.prompt_surface import (
    _DEFAULT_PLACEHOLDER_TEXT,
    resolve_prompt_placeholder,
)
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskKind


def _placeholder_text(session: ReplSession) -> str:
    return resolve_prompt_placeholder(session).value


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
