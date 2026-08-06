"""Unit tests for the action-turn LLM goal reviewer (turns/goal_review.py)."""

from __future__ import annotations

from typing import Any, cast

from core.agent import Agent
from core.agent.goals import GoalObservation, should_accept_with_goal
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.turns.goal_review import build_goal_reviewer
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.registered_tool import RegisteredTool

_MAX_ITERATIONS = 13


def _text(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _tool_call(call_id: str, name: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, input={})],
        raw_content=None,
    )


class _FakeReviewLLM:
    """Returns scripted review verdicts and counts invocations."""

    def __init__(self, verdicts: list[str]) -> None:
        self._verdicts = iter(verdicts)
        self.invocations = 0
        self.model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:  # noqa: ARG002
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.invocations += 1
        return _text(next(self._verdicts))


class _RaisingLLM:
    """Review LLM whose invoke always fails."""

    model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:  # noqa: ARG002
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        raise RuntimeError("provider down")


def _observation(final_text: str = "Listed the loops.", evidence_count: int = 1) -> GoalObservation:
    return GoalObservation(
        final_text=final_text,
        evidence_count=evidence_count,
        iteration=0,
        max_iterations=_MAX_ITERATIONS,
    )


def test_goal_reached_verdict_accepts() -> None:
    llm = _FakeReviewLLM(["GOAL_REACHED"])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")

    assert goal.verify is not None
    assert goal.verify(_observation()) is True
    assert llm.invocations == 1


def test_not_reached_verdict_rejects_and_nudges() -> None:
    llm = _FakeReviewLLM(["NOT_REACHED"])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")

    accept, nudge = should_accept_with_goal(
        goal,
        final_text="Listed the loops.",
        evidence_count=1,
        iteration=0,
        max_iterations=_MAX_ITERATIONS,
    )

    assert accept is False
    assert nudge is not None
    assert "remove the existing cron loops" in nudge


def test_no_tool_work_skips_review() -> None:
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")

    assert goal.verify is not None
    assert goal.verify(_observation(evidence_count=0)) is True
    assert llm.invocations == 0


def test_closing_question_skips_review() -> None:
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")

    assert goal.verify is not None
    assert goal.verify(_observation(final_text="Found one loop — remove it?")) is True
    assert llm.invocations == 0


def test_review_llm_failure_fails_open() -> None:
    goal = build_goal_reviewer(_RaisingLLM(), "remove the existing cron loops")

    assert goal.verify is not None
    assert goal.verify(_observation()) is True


def test_review_budget_caps_rejections() -> None:
    llm = _FakeReviewLLM(["NOT_REACHED", "NOT_REACHED", "NOT_REACHED"])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")

    assert goal.verify is not None
    assert goal.verify(_observation()) is False
    assert goal.verify(_observation()) is False
    # Budget exhausted: accept without another review call.
    assert goal.verify(_observation()) is True
    assert llm.invocations == 2


def test_build_agent_passes_goal_through() -> None:
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(llm, "remove the existing cron loops")
    config: AgentConfig[RegisteredTool] = AgentConfig(
        llm=llm,
        system="sys",
        tools=(),
        resolved_integrations={},
        max_iterations=1,
        goal=goal,
    )

    agent = build_agent(config)

    assert agent._goal is goal


class _ScriptedActionLLM:
    """Serves the loop and the reviewer from one client, like the real wiring.

    Review calls are distinguishable because the reviewer passes no ``tools``
    payload, while the loop always sends its (possibly empty) schema list.
    """

    def __init__(self, main: list[AgentLLMResponse], verdicts: list[str]) -> None:
        self._main = iter(main)
        self._verdicts = iter(verdicts)
        self.review_prompts: list[str] = []
        self.model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": t.name} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        if tools is None:
            self.review_prompts.append(str(messages[0]["content"]))
            return _text(next(self._verdicts))
        return next(self._main)

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": output} for tc, output in zip(tool_calls, results)],
        }


class _FakeTool:
    """Minimal stand-in exposing only what tool execution touches."""

    def __init__(self, name: str) -> None:
        self.name = name

    def validate_public_input(self, value: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"ok": True}


def test_loop_nudges_stopped_short_turn_until_goal_reached() -> None:
    """The cron regression: listing must not satisfy a removal request.

    The agent lists, concludes, gets a NOT_REACHED verdict, is nudged to
    continue, removes, and only then concludes with an accepted answer.
    """
    llm = _ScriptedActionLLM(
        main=[
            _tool_call("c1", "slash_invoke"),
            _text("Listed the existing cron loops."),
            _tool_call("c2", "slash_invoke"),
            _text("Removed cron loop 21548d353aca."),
        ],
        verdicts=["NOT_REACHED", "GOAL_REACHED"],
    )
    goal = build_goal_reviewer(llm, "remove the existing cron loops")
    tools = cast("list[RegisteredTool]", [_FakeTool("slash_invoke")])
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=6,
        goal=goal,
    )

    result = agent.run([{"role": "user", "content": "remove the existing cron loops"}])

    assert result.final_text == "Removed cron loop 21548d353aca."
    assert [tc.name for tc, _output in result.executed] == ["slash_invoke", "slash_invoke"]
    assert len(llm.review_prompts) == 2
    assert "User goal: remove the existing cron loops" in llm.review_prompts[0]
