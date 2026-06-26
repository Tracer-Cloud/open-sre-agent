"""Tests for the tool-gathering wrapper used by interactive-shell execution.

``_answer_cli_agent_with_tools`` runs a tool-gathering pass on the main fallback
path (no pre-existing ``tool_observation``) and threads any collected evidence
into ``answer_cli_agent`` as an off-screen observation. The summarize path (a
``tool_observation`` already supplied) is passed through unchanged. These tests
patch the canonical ``runtime.execution`` seams so no LLM or tools run.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

import cli.interactive_shell.runtime.execution as execution
from cli.interactive_shell.runtime.session import ReplSession


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


def _record_answer(monkeypatch: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_answer(message: str, session: ReplSession, console: Console, **kwargs: Any) -> None:
        calls.append({"message": message, **kwargs})
        return None

    monkeypatch.setattr(execution, "answer_cli_agent", _fake_answer)
    return calls


def test_gather_string_threads_offscreen_observation(monkeypatch: Any) -> None:
    calls = _record_answer(monkeypatch)
    monkeypatch.setattr(
        execution, "gather_tool_evidence", lambda *_a, **_k: "Tool: x\nArguments: {}\nResult: y"
    )

    execution._answer_cli_agent_with_tools("question", ReplSession(), _console())

    assert len(calls) == 1
    assert calls[0]["tool_observation"] == "Tool: x\nArguments: {}\nResult: y"
    assert calls[0]["tool_observation_on_screen"] is False


def test_gather_none_passes_through_without_observation(monkeypatch: Any) -> None:
    calls = _record_answer(monkeypatch)
    monkeypatch.setattr(execution, "gather_tool_evidence", lambda *_a, **_k: None)

    execution._answer_cli_agent_with_tools("question", ReplSession(), _console())

    assert len(calls) == 1
    assert calls[0]["tool_observation"] is None
    assert "tool_observation_on_screen" not in calls[0]


def test_existing_observation_skips_gather(monkeypatch: Any) -> None:
    calls = _record_answer(monkeypatch)

    def _should_not_run(*_a: Any, **_k: Any) -> str:
        raise AssertionError("gather_tool_evidence must not run on the summarize path")

    monkeypatch.setattr(execution, "gather_tool_evidence", _should_not_run)

    execution._answer_cli_agent_with_tools(
        "question", ReplSession(), _console(), tool_observation="already gathered"
    )

    assert len(calls) == 1
    assert calls[0]["tool_observation"] == "already gathered"
