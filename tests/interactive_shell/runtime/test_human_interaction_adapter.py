"""Tests for prompt-mediated REPL human hand-offs."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.human_interaction_adapter as adapter
from core.agent_harness.human_interaction import UserChoiceOption, UserChoiceRequest

_REQUEST = UserChoiceRequest(
    id="deployment_strategy",
    header="Deployment",
    question="Which deployment strategy should we use?",
    options=(
        UserChoiceOption("Canary (Recommended)", "Limit the initial blast radius."),
        UserChoiceOption("Blue-green", "Keep a complete rollback environment."),
        UserChoiceOption("Rolling", "Update instances incrementally."),
    ),
)


def _console() -> tuple[Console, io.StringIO]:
    output = io.StringIO()
    return Console(file=output, force_terminal=False, highlight=False), output


def test_numbered_choice_returns_canonical_label() -> None:
    console, output = _console()
    port = adapter.ReplHumanInteractionPort(console, lambda _prompt, **_kwargs: "2")

    answer = port.choose(_REQUEST)

    assert answer == "Blue-green"
    rendered = output.getvalue()
    assert _REQUEST.question in rendered
    assert "Limit the initial blast radius." in rendered
    assert "Other" in rendered


def test_label_and_custom_answers_are_accepted() -> None:
    console, _output = _console()
    label_port = adapter.ReplHumanInteractionPort(
        console,
        lambda _prompt, **_kwargs: "rolling",
    )
    custom_port = adapter.ReplHumanInteractionPort(
        console,
        lambda _prompt, **_kwargs: "Shadow traffic",
    )

    assert label_port.choose(_REQUEST) == "Rolling"
    assert custom_port.choose(_REQUEST) == "Shadow traffic"


def test_factory_requires_interactive_prompt_input(monkeypatch: pytest.MonkeyPatch) -> None:
    console, _output = _console()
    monkeypatch.setattr(adapter, "repl_tty_interactive", lambda: False)

    assert (
        adapter.repl_human_interaction_factory(
            console,
            lambda _prompt, **_kwargs: "1",
            None,
        )
        is None
    )
    assert adapter.repl_human_interaction_factory(console, None, True) is None


def test_choice_requests_accept_arbitrary_prompt_input() -> None:
    console, _output = _console()
    accepts_any_values: list[bool] = []

    def _prompt_input(
        _prompt: str,
        *,
        accepts_any_answer: bool = False,
    ) -> str:
        accepts_any_values.append(accepts_any_answer)
        return "Shadow traffic"

    port = adapter.ReplHumanInteractionPort(console, _prompt_input)

    assert port.choose(_REQUEST) == "Shadow traffic"
    assert accepts_any_values == [True]
