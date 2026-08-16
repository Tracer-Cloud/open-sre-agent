"""Service provider interface: what a host consumes to integrate a turn.

Session-goal state, continuation and progress; per-turn accounting; the
cancellation hook; chat-turn bindings and dispatch; turn-result types; and the
built-in prompt provider a host extends. Import-cheap: nothing here loads the
agent loop. Building and running the agent is :mod:`core.agent_harness.runtime`.
"""

from __future__ import annotations

from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
from core.agent_harness.session.integration_resolution import has_resolved_integrations
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.progress import (
    format_session_goal_progress,
    format_session_goal_status_line,
)
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.host_cancel import ensure_turn_cancel
from core.agent_harness.turns.orchestrator import stream_answer
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

__all__ = [
    "ChatTurnBindings",
    "DefaultPromptContextProvider",
    "DefaultTurnAccounting",
    "SessionGoal",
    "ToolCallingTurnResult",
    "TurnResult",
    "dispatch_chat_turn",
    "ensure_turn_cancel",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "has_resolved_integrations",
    "run_until_session_goal",
    "stream_answer",
]
