"""AgentSession.chat_until_goal wraps the SessionGoal loop."""

from __future__ import annotations

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.goal import SessionGoal, SessionGoalStatus
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch(self, message: str) -> TurnResult:
        self.calls.append(message)
        body = "working session_goal:done=0" if len(self.calls) == 1 else "done session_goal:done=1"
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=body,
        )


def test_chat_until_goal_continues_until_achieved() -> None:
    dispatcher = _FakeDispatcher()
    api = AgentSession(SessionConfig(load_env=False))
    api._bound_session = SessionCore()
    api.attach_agent(dispatcher)  # type: ignore[arg-type]

    outcome = api.chat_until_goal(
        "go",
        goal=SessionGoal(
            condition="two-step",
            max_outer_turns=3,
            checklist=("one", "two"),
        ),
    )

    assert len(dispatcher.calls) == 2
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert "session_goal:" not in (outcome.last_result.assistant_response_text or "")
