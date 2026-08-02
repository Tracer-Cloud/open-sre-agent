"""Architecture guards for the two agent shapes documented in AGENTS.md."""

from __future__ import annotations

import inspect
from typing import get_type_hints

from core.agent import Agent
from core.agent_harness.ports import ExecuteActions, StreamAnswerFn
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.orchestrator import run_turn, stream_answer


def _accept_answer(answer: StreamAnswerFn) -> StreamAnswerFn:
    return answer


def _accept_execute_actions(driver: ExecuteActions) -> ExecuteActions:
    return driver


def test_stream_answer_matches_stream_answer_fn_seam() -> None:
    # stream_answer takes session/output/prompts first; the bound seam is thinner.
    # Pin that HeadlessAgent._answer matches the Protocol shape run_turn uses.
    agent = HeadlessAgent(tools=NullToolProvider())
    accepted = _accept_answer(agent._answer)
    assert accepted.__func__ is HeadlessAgent._answer


def test_headless_agent_execute_actions_matches_execute_actions_seam() -> None:
    agent = HeadlessAgent(tools=NullToolProvider())
    # Bound methods are new objects per access; the seam is the underlying function.
    accepted = _accept_execute_actions(agent._execute_actions)
    assert accepted.__func__ is HeadlessAgent._execute_actions


def test_run_turn_wires_streaming_and_tool_calling_seams() -> None:
    hints = get_type_hints(run_turn)
    assert hints["answer"] is StreamAnswerFn
    assert hints["execute_actions"] is ExecuteActions


def test_agent_is_tool_calling_shape() -> None:
    assert callable(getattr(Agent, "run", None))


def test_streaming_answer_is_not_tool_calling_shape() -> None:
    assert getattr(stream_answer, "run", None) is None


def test_stream_answer_entrypoint_doc_names_direct_answer_shape() -> None:
    doc = inspect.getdoc(stream_answer) or ""
    assert "direct answer" in doc
    assert "tool-calling" in doc
