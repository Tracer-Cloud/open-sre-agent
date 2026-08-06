"""Unit tests for the action- and gather-turn LLM goal reviewers (turns/goal_review.py)."""

from __future__ import annotations

from typing import Any, cast

from core.agent import Agent
from core.agent.goals import GoalObservation, should_accept_with_goal
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.turns.goal_review import (
    build_gather_goal_reviewer,
    build_goal_reviewer,
    tap_executed_tool_names,
)
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
    goal = build_goal_reviewer(llm, "remove the existing cron loops", ["slash_invoke"])

    assert goal.verify is not None
    assert goal.verify(_observation()) is True
    assert llm.invocations == 1


def test_not_reached_verdict_rejects_and_nudges() -> None:
    llm = _FakeReviewLLM(["NOT_REACHED"])
    goal = build_goal_reviewer(llm, "remove the existing cron loops", ["slash_invoke"])

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
    goal = build_goal_reviewer(llm, "remove the existing cron loops", [])

    assert goal.verify is not None
    assert goal.verify(_observation(evidence_count=0)) is True
    assert llm.invocations == 0


def test_closing_question_skips_review() -> None:
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(llm, "remove the existing cron loops", ["slash_invoke"])

    assert goal.verify is not None
    assert goal.verify(_observation(final_text="Found one loop — remove it?")) is True
    assert llm.invocations == 0


def test_investigation_dispatch_skips_review() -> None:
    """Async dispatch is the turn's goal; results arrive later, so no nudge."""
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(
        llm,
        "find out why checkout is failing",
        ["slash_invoke", "investigation_start"],
    )

    assert goal.verify is not None
    assert goal.verify(_observation(final_text="Investigation started.", evidence_count=2)) is True
    assert llm.invocations == 0


def test_assistant_handoff_skips_review() -> None:
    """A handoff means the conversational assistant owns the reply."""
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(
        llm,
        "checkout api is returning 500s since 14:05 utc",
        ["assistant_handoff"],
    )

    assert goal.verify is not None
    assert goal.verify(_observation(final_text="Handing off.", evidence_count=1)) is True
    assert llm.invocations == 0


def test_review_llm_failure_fails_open() -> None:
    goal = build_goal_reviewer(_RaisingLLM(), "remove the existing cron loops", ["slash_invoke"])

    assert goal.verify is not None
    assert goal.verify(_observation()) is True


def test_review_runs_at_most_once_per_turn() -> None:
    llm = _FakeReviewLLM(["NOT_REACHED", "NOT_REACHED"])
    goal = build_goal_reviewer(llm, "remove the existing cron loops", ["slash_invoke"])

    assert goal.verify is not None
    assert goal.verify(_observation()) is False
    # Budget exhausted: accept without another review call.
    assert goal.verify(_observation()) is True
    assert llm.invocations == 1


def test_build_agent_passes_goal_through() -> None:
    llm = _FakeReviewLLM([])
    goal = build_goal_reviewer(llm, "remove the existing cron loops", [])
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

    Wired like the action driver — the runtime-event tap feeds executed tool
    names to the reviewer. The agent lists, concludes, gets a NOT_REACHED
    verdict, is nudged to continue, removes, and concludes; the follow-up
    conclusion is accepted without a second review (one review per turn).
    """
    llm = _ScriptedActionLLM(
        main=[
            _tool_call("c1", "slash_invoke"),
            _text("Listed the existing cron loops."),
            _tool_call("c2", "slash_invoke"),
            _text("Removed cron loop 21548d353aca."),
        ],
        verdicts=["NOT_REACHED"],
    )
    executed_tool_names: list[str] = []
    goal = build_goal_reviewer(llm, "remove the existing cron loops", executed_tool_names)
    tools = cast("list[RegisteredTool]", [_FakeTool("slash_invoke")])
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=6,
        on_runtime_event=tap_executed_tool_names(None, executed_tool_names),
        goal=goal,
    )

    result = agent.run([{"role": "user", "content": "remove the existing cron loops"}])

    assert result.final_text == "Removed cron loop 21548d353aca."
    assert [tc.name for tc, _output in result.executed] == ["slash_invoke", "slash_invoke"]
    assert executed_tool_names == ["slash_invoke", "slash_invoke"]
    assert llm.review_prompts == [
        "User goal: remove the existing cron loops\n"
        "Actions executed this turn: 1\n"
        "Agent's closing reply:\nListed the existing cron loops."
    ]


# ---------------------------------------------------------------------------
# Gather-flavored reviewer (evidence-gather turns)
# ---------------------------------------------------------------------------


def test_gather_not_reached_rejects_and_nudges_with_gather_criteria() -> None:
    llm = _FakeReviewLLM(["NOT_REACHED"])
    goal = build_gather_goal_reviewer(llm, "how many windows users do we have?")

    accept, nudge = should_accept_with_goal(
        goal,
        final_text="PostHog is reachable; execute-sql and query-trends are available.",
        evidence_count=3,
        iteration=1,
        max_iterations=6,
    )

    assert accept is False
    assert nudge is not None
    assert "how many windows users do we have?" in nudge
    assert "tool listings and schema metadata are only preparation" in nudge.lower()


def test_gather_reviewer_reviews_closing_questions() -> None:
    """No user answers mid-gather, so a '?' conclusion is still reviewed."""
    llm = _FakeReviewLLM(["NOT_REACHED"])
    goal = build_gather_goal_reviewer(llm, "how many windows users do we have?")

    assert goal.verify is not None
    assert goal.verify(_observation(final_text="Should I run execute-sql?")) is False
    assert llm.invocations == 1


def test_gather_reviewer_skips_when_no_tools_ran() -> None:
    llm = _FakeReviewLLM([])
    goal = build_gather_goal_reviewer(llm, "how many windows users do we have?")

    assert goal.verify is not None
    assert goal.verify(_observation(evidence_count=0)) is True
    assert llm.invocations == 0


def test_gather_loop_nudges_discovery_only_conclusion_until_data_fetched() -> None:
    """The PostHog regression: a tool listing must not satisfy a data question.

    The agent lists MCP tools, concludes on discovery metadata, gets a
    NOT_REACHED verdict, is nudged to continue, executes the actual query, and
    concludes with the data.
    """
    llm = _ScriptedActionLLM(
        main=[
            _tool_call("c1", "list_posthog_tools"),
            _text("PostHog MCP is reachable; execute-sql is available."),
            _tool_call("c2", "call_posthog_tool"),
            _text("1,204 unique Windows users in the last 30 days."),
        ],
        verdicts=["NOT_REACHED"],
    )
    goal = build_gather_goal_reviewer(llm, "how many windows users do we have?")
    tools = cast(
        "list[RegisteredTool]",
        [_FakeTool("list_posthog_tools"), _FakeTool("call_posthog_tool")],
    )
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=6,
        goal=goal,
    )

    result = agent.run([{"role": "user", "content": "how many windows users do we have?"}])

    assert result.final_text == "1,204 unique Windows users in the last 30 days."
    assert [tc.name for tc, _output in result.executed] == [
        "list_posthog_tools",
        "call_posthog_tool",
    ]
    assert len(llm.review_prompts) == 1
    assert "how many windows users do we have?" in llm.review_prompts[0]
