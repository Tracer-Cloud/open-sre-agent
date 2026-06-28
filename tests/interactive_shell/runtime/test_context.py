"""Tests for validated interactive-shell runtime context assembly."""

from __future__ import annotations

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from pydantic import ValidationError

from interactive_shell.controller import InteractiveShellController
from interactive_shell.runtime.core.state import ReplState, SpinnerState, create_repl_mutable_state
from interactive_shell.runtime.core.tasks import TaskRegistry
from interactive_shell.session import (
    ReplRuntimeContext,
    ReplSession,
    ReplSessionBootstrapSpec,
    create_repl_runtime_context,
)


def _prompt_session() -> PromptSession[str]:
    return PromptSession(history=InMemoryHistory())


def test_create_context_applies_canonical_session_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = TaskRegistry()
    hydrate_calls: list[str] = []

    def _hydrate(self: ReplSession) -> None:
        hydrate_calls.append(self.session_id)
        self.configured_integrations = ("github",)
        self.configured_integrations_known = True

    monkeypatch.setattr(ReplSession, "hydrate_configured_integrations", _hydrate)
    monkeypatch.setattr(TaskRegistry, "persistent", staticmethod(lambda: registry))

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = _prompt_session()
        session = ReplSession()
        context = create_repl_runtime_context(
            session=session,
            pt_session=prompt,
            active_theme_name="pink",
        )

    assert context.session is session
    assert session.active_theme_name == "pink"
    assert session.configured_integrations == ("github",)
    assert session.configured_integrations_known is True
    assert hydrate_calls == [session.session_id]
    assert session.task_registry is registry
    assert session.prompt_history_backend is prompt.history


def test_context_supports_lightweight_bootstrap_for_unit_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ReplSession,
        "hydrate_configured_integrations",
        lambda _self: (_ for _ in ()).throw(AssertionError("hydrated")),
    )
    monkeypatch.setattr(
        TaskRegistry,
        "persistent",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("persistent"))),
    )

    context = create_repl_runtime_context(
        active_theme_name="green",
        hydrate_integrations=False,
        persistent_tasks=False,
    )

    assert context.session.active_theme_name == "green"
    assert isinstance(context.state, ReplState)
    assert isinstance(context.spinner, SpinnerState)


def test_create_repl_mutable_state_returns_fresh_initial_state() -> None:
    first = create_repl_mutable_state()
    second = create_repl_mutable_state()

    assert isinstance(first.state, ReplState)
    assert isinstance(first.spinner, SpinnerState)
    assert first.state is not second.state
    assert first.spinner is not second.spinner
    assert first.state.exit_requested is False
    assert first.state.is_dispatch_running() is False
    assert first.state.is_awaiting_confirmation() is False
    assert first.spinner.streaming is False


def test_context_uses_canonical_initial_mutable_state() -> None:
    context = ReplRuntimeContext(session=ReplSession())

    assert isinstance(context.state, ReplState)
    assert isinstance(context.spinner, SpinnerState)
    assert context.state.exit_requested is False
    assert context.spinner.streaming is False


def test_context_preserves_explicit_partial_mutable_state() -> None:
    state = ReplState()
    context = ReplRuntimeContext(session=ReplSession(), state=state)

    assert context.state is state
    assert isinstance(context.spinner, SpinnerState)


def test_context_rejects_invalid_state_contracts() -> None:
    with pytest.raises(ValidationError):
        ReplRuntimeContext(session=object())  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ReplSessionBootstrapSpec(active_theme_name=" ")


def test_context_assignment_validates_inbox_type() -> None:
    context = ReplRuntimeContext(session=ReplSession())

    with pytest.raises(ValidationError):
        context.inbox = object()  # type: ignore[assignment]


def test_controller_reuses_validated_runtime_context() -> None:
    session = ReplSession()
    state = ReplState()
    spinner = SpinnerState()
    context = ReplRuntimeContext(session=session, state=state, spinner=spinner)

    controller = InteractiveShellController(context)

    assert controller.runtime_context is context
    assert controller.session is session
    assert controller.state is state
    assert controller.spinner is spinner
    assert controller.input_reader.state is state
