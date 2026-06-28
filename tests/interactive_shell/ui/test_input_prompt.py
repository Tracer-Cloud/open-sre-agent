"""Tests for prompt placeholder and prefill behavior."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest
from prompt_toolkit.completion import Completion

from interactive_shell.runtime.core import state as loop_state
from interactive_shell.session import ReplSession
from interactive_shell.ui.input_prompt import completion as prompt_completion
from interactive_shell.ui.input_prompt import rendering as prompt_rendering
from interactive_shell.ui.input_prompt.completion import completion_preview_hint_ansi
from interactive_shell.ui.input_prompt.refresh import wire_prompt_refresh
from interactive_shell.ui.input_prompt.rendering import (
    _DEFAULT_PLACEHOLDER_TEXT,
    _prompt_counter_text,
    _prompt_turn_number,
    resolve_idle_hint_ansi,
    resolve_prompt_placeholder,
    resolve_prompt_prefix_ansi,
)
from platform.common.task_types import TaskKind


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _placeholder_text(session: ReplSession) -> str:
    return resolve_prompt_placeholder(session).value


class _RefreshFakeBuffer:
    def __init__(self) -> None:
        self.text = ""
        self.submitted = False

    def validate_and_handle(self) -> None:
        self.submitted = True


class _RefreshFakeApp:
    is_running = True

    def __init__(self) -> None:
        self.current_buffer = _RefreshFakeBuffer()

    def invalidate(self) -> None:
        pass


class _RefreshFakeLoop:
    def call_soon_threadsafe(self, fn, *args) -> None:  # type: ignore[no-untyped-def]
        fn(*args)


class TestPromptRefreshAutoSubmit:
    def test_queue_auto_command_fills_and_submits_prompt(self) -> None:
        """An agent-queued interactive command should be both prefilled and
        auto-submitted so it dispatches through the exclusive-stdin path."""
        session = ReplSession()
        app = _RefreshFakeApp()
        wire_prompt_refresh(session, app, _RefreshFakeLoop())
        session.queue_auto_command("/integrations setup sentry")
        assert app.current_buffer.text == "/integrations setup sentry"
        assert app.current_buffer.submitted is True

    def test_plain_prefill_does_not_auto_submit(self) -> None:
        """A prefill without the auto-submit flag must wait for the user (Enter)."""
        session = ReplSession()
        app = _RefreshFakeApp()
        wire_prompt_refresh(session, app, _RefreshFakeLoop())
        session.pending_prompt_default = "why did it fail?"
        session.notify_prompt_changed()
        assert app.current_buffer.text == "why did it fail?"
        assert app.current_buffer.submitted is False


class TestPromptTurnCounter:
    def test_first_turn_is_numbered_one(self) -> None:
        session = ReplSession()
        assert _prompt_turn_number(session) == 1
        assert _prompt_counter_text(session) == "[1] "

    def test_counter_advances_with_history(self) -> None:
        session = ReplSession()
        session.record("chat", "hello")
        assert _prompt_turn_number(session) == 2
        assert _prompt_counter_text(session) == "[2] "


class TestResolveIdleHint:
    def test_shows_connected_integrations_in_hint_bar(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ("datadog", "github", "grafana")
        rendered = _strip_ansi(resolve_idle_hint_ansi(session))
        assert "/ for commands" in rendered
        assert "Datadog" in rendered
        assert "GitHub" in rendered
        assert "Grafana" in rendered

    def test_omits_integrations_when_none_configured(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ()
        rendered = _strip_ansi(resolve_idle_hint_ansi(session))
        assert "Datadog" not in rendered
        assert "/ for commands" in rendered


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


@dataclass
class _FakeCompleteState:
    completions: list[Completion]
    current_completion: Completion | None = None


@dataclass
class _FakeBuffer:
    text: str
    complete_state: _FakeCompleteState | None = None


@dataclass
class _FakeOutput:
    columns: int = 120

    def get_size(self) -> _FakeOutput:
        return self


@dataclass
class _FakeApp:
    current_buffer: _FakeBuffer
    output: _FakeOutput


class TestCompletionPreviewHint:
    def test_returns_empty_when_no_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: None)
        assert completion_preview_hint_ansi() == ""

    def test_shows_full_slash_command_description(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completion = Completion(
            "/investigate",
            start_position=-1,
            display="/investigate",
            display_meta="Run an RCA investigation from a file or sample templa…",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered.startswith("/investigate — ")
        assert len(rendered) > len("/investigate — " + completion.display_meta_text)
        assert "…" not in rendered

    def test_unregistered_slash_completion_uses_display_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completion = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta="Plugin-provided slash command.",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/plugin-cmd — Plugin-provided slash command."

    def test_shows_subcommand_label_with_parent_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completion = Completion(
            "high",
            start_position=-1,
            display="high",
            display_meta="favor more thorough reasoning",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/effort ",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/effort high — favor more thorough reasoning"

    def test_falls_back_to_first_completion_when_none_selected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta="Plugin-provided slash command.",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[first],
                    current_completion=None,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/plugin-cmd — Plugin-provided slash command."

    def test_clips_preview_to_terminal_width(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long_meta = (
            "Plugin-provided slash command with a deliberately long description "
            "that must be clipped to the terminal width."
        )
        completion = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta=long_meta,
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(columns=40),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered.endswith("…")
        assert len(rendered) <= 40
        assert rendered.startswith("/plugin-cmd — ")


class TestResolvePromptPrefix:
    def test_prefers_inline_spinner_over_completion_preview(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            prompt_rendering,
            "completion_preview_hint_ansi",
            lambda: "preview line",
        )
        spinner = loop_state.SpinnerState()
        spinner.start()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        assert "preview line" not in prefix
        assert "esc to cancel" in _strip_ansi(prefix)

    def test_prefers_completion_preview_over_idle_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            prompt_rendering,
            "completion_preview_hint_ansi",
            lambda: "preview line",
        )
        spinner = loop_state.SpinnerState()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        assert prefix == "preview line"
        assert "/ for commands" not in prefix

    def test_falls_back_to_idle_hint_when_no_preview(self) -> None:
        spinner = loop_state.SpinnerState()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        assert "/ for commands" in _strip_ansi(prefix)
