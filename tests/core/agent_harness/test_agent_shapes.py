"""Architecture guards for the two agent shapes documented in AGENTS.md."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from core.agent import Agent
from core.agent_harness.models.turn_results import ToolCallingTurnResult
from core.agent_harness.ports import ExecuteActions, StreamAnswerFn
from core.agent_harness.turns.action_driver import run_action_agent_turn
from core.agent_harness.turns.orchestrator import run_turn, stream_answer


def _accept_answer(answer: StreamAnswerFn) -> StreamAnswerFn:
    return answer


def _accept_execute_actions(driver: ExecuteActions) -> ExecuteActions:
    return driver


def test_stream_answer_matches_stream_answer_fn_seam() -> None:
    assert _accept_answer(stream_answer) is stream_answer


def test_run_action_agent_turn_matches_execute_actions_seam() -> None:
    assert _accept_execute_actions(run_action_agent_turn) is run_action_agent_turn


def test_run_turn_wires_streaming_and_tool_calling_seams() -> None:
    hints = get_type_hints(run_turn)
    assert hints["answer"] == Callable[..., Any]
    assert hints["execute_actions"] == Callable[..., ToolCallingTurnResult]


def test_agent_is_tool_calling_shape() -> None:
    assert callable(getattr(Agent, "run", None))


def test_streaming_answer_is_not_tool_calling_shape() -> None:
    assert getattr(stream_answer, "run", None) is None


def test_stream_answer_entrypoint_doc_names_direct_answer_shape() -> None:
    doc = inspect.getdoc(stream_answer) or ""
    assert "direct answer" in doc
    assert "tool-calling" in doc
