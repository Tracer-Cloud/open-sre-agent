"""Repro: an informational model question routed to /model never gets answered.

Reproduces the REPL transcript where the user typed "which model is being used
now?" and the turn rendered ``Requested actions: 1. command /model`` / ``$ /model``
but never produced a conversational answer.

The selection itself (the action agent choosing ``slash_invoke("/model")`` for an
informational question) is exercised by the live planning scenario
``chat_handoff/342-which-model-is-used-now``. These tests pin the *deterministic*
half: given that the action agent picks ``/model``, the turn ends without
answering the user, because ``/model`` records no ``last_command_observation`` and
so the turn router (``core.agent_harness.turns.orchestrator._route_turn``) takes the
``handled_without_llm`` path instead of summarizing an observation.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from rich.console import Console

import core.llm.llm_client as llm_module
import surfaces.interactive_shell.runtime.shell_turn_execution as shell_turn_execution
import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.providers import default_prompt_context
from core.agent_harness.providers.default_prompt_context import DefaultPromptContextProvider
from core.agent_harness.session import Session
from surfaces.interactive_shell.command_registry import dispatch_slash
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    tool_response,
)

_ACTION_LLM_FACTORY_PATCH = "surfaces.interactive_shell.runtime.action_turn.default_llm_factory"
_PROMPT = "which model is being used now?"


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=100), buf


def test_model_show_records_no_observation() -> None:
    """Root cause: /model does not stash a discovery observation.

    The observe->answer summary loop only fires when a discovery slash command
    sets ``last_command_observation`` (integrations list/verify do). /model does
    not, so a turn that runs /model has nothing to summarize into an answer.
    """
    session = Session()
    console, _ = _capture()
    session.last_command_observation = "stale"
    session.last_command_observation = None

    dispatch_slash("/model show", session, console)

    assert session.last_command_observation is None


def test_model_question_routed_to_slash_is_never_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-turn repro: action agent picks /model -> turn handled, no answer.

    When the action agent selects ``slash_invoke("/model")`` for the
    informational question, ``/model`` runs and the turn is marked handled. Because
    no observation was recorded, the conversational assistant is never invoked, so
    the user's actual question ("which model is being used now?") goes unanswered —
    exactly the transcript behavior.
    """
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"$ {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(
        _ACTION_LLM_FACTORY_PATCH,
        lambda: FakeActionLLM([tool_response("slash_invoke", {"command": "/model", "args": []})]),
    )

    answer_calls: list[str] = []

    def _spy_answer(text: str, *_args: object, **_kwargs: object) -> None:
        answer_calls.append(text)
        return None

    session = Session()
    console, buf = _capture()
    result = shell_turn_execution.execute_shell_turn(
        _PROMPT,
        session,
        console,
        recorder=None,
        answer_agent=_spy_answer,
    )

    # The action agent's /model choice executed and "handled" the turn.
    assert dispatched == ["/model"]
    assert result.action_result.handled is True

    # The bug: the conversational assistant was never called, so the user's
    # question is never answered (no summary pass, no fallback answer).
    assert answer_calls == []
    assert result.final_intent == "cli_agent_handled"
    assert result.answered is False

    output = buf.getvalue()
    assert "$ /model" in output


def test_model_question_answered_when_handed_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contrast / desired behavior: an assistant_handoff lets the turn answer.

    If the action agent had instead emitted ``assistant_handoff`` (or no action),
    the turn falls through to the conversational assistant, which answers the
    question. This pins the intended outcome the misroute breaks.
    """

    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("handoff turn must not dispatch a slash command")

    monkeypatch.setattr(slash_tool, "dispatch_slash", _unexpected_dispatch)
    monkeypatch.setattr(
        _ACTION_LLM_FACTORY_PATCH,
        lambda: FakeActionLLM([tool_response("assistant_handoff", {"content": "chat:model"})]),
    )

    answer_calls: list[str] = []

    def _spy_answer(text: str, *_args: object, **_kwargs: object) -> object:
        answer_calls.append(text)
        return object()

    session = Session()
    console, _ = _capture()
    result = shell_turn_execution.execute_shell_turn(
        _PROMPT,
        session,
        console,
        recorder=None,
        gather_evidence=lambda *_a, **_k: None,
        answer_agent=_spy_answer,
    )

    assert result.action_result.handled is False
    assert answer_calls == [_PROMPT]
    assert result.answered is True


def test_model_question_handoff_answers_from_active_llm_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixed UX: handoff prompt carries model settings and no internal action preview."""

    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("natural-language model question must not dispatch /model")

    class _Settings:
        provider = "openai"

    class _LLM:
        last_prompt: str | None = None

        def invoke_stream(self, prompt: str) -> Iterator[str]:
            self.last_prompt = prompt
            yield "You are using OpenAI with reasoning model `gpt-5.5` and tool-call model `gpt-5.4-mini`."

    llm = _LLM()
    monkeypatch.setattr(slash_tool, "dispatch_slash", _unexpected_dispatch)
    monkeypatch.setattr(
        _ACTION_LLM_FACTORY_PATCH,
        lambda: FakeActionLLM([tool_response("assistant_handoff", {"content": "chat:model"})]),
    )
    monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: llm)
    monkeypatch.setattr(default_prompt_context, "load_llm_settings", lambda: _Settings())
    monkeypatch.setattr(
        default_prompt_context,
        "resolve_provider_models",
        lambda _settings, _provider: ("gpt-5.5", "gpt-5.4-mini"),
    )
    provider = DefaultPromptContextProvider
    monkeypatch.setattr(provider, "cli_reference", lambda _self: "(ref)")
    monkeypatch.setattr(provider, "agents_md", lambda _self: "")
    monkeypatch.setattr(provider, "investigation_flow", lambda _self: "")

    session = Session()
    session.cli_agent_messages = [
        ("user", "/model"),
        (
            "assistant",
            "switched LLM provider: openai\nreasoning model: gpt-5.5\ntoolcall model: gpt-5.4-mini",
        ),
    ]
    console, buf = _capture()
    result = shell_turn_execution.execute_shell_turn(
        _PROMPT,
        session,
        console,
        recorder=None,
        gather_evidence=lambda *_a, **_k: None,
    )

    assert result.action_result.handled is False
    assert result.answered is True
    assert "gpt-5.5" in result.assistant_response_text
    assert "gpt-5.4-mini" in result.assistant_response_text
    assert llm.last_prompt is not None
    assert "Active LLM settings in this session" in llm.last_prompt
    assert "provider openai" in llm.last_prompt
    assert "reasoning model gpt-5.5" in llm.last_prompt
    assert "tool-call model gpt-5.4-mini" in llm.last_prompt

    output = buf.getvalue()
    assert "Requested actions" not in output
    assert "$ /model" not in output
    assert "/model" not in result.assistant_response_text
