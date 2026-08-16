"""Service provider interface: what a host consumes to integrate a turn.

Session-goal state, lifecycle, continuation and progress; session flags a host
reads and sets around a turn (background and trust mode, terminal, auto-command,
turn-outcome hints); per-turn and per-run accounting; the cancellation hook;
chat-turn bindings and dispatch; turn-result types; the want-me-to closer;
integration resolution; grounding-cache observability; the action-skill
catalog; the built-in prompt provider a host extends; and the default session
store and repository. Import-cheap: nothing here loads the agent
loop. Building and running the agent is :mod:`core.agent_harness.runtime`.
"""

from __future__ import annotations

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.accounting.token_accounting import (
    LlmRunInfo,
    format_token_total,
    record_llm_turn,
)
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.grounding.diagnostics import (
    GroundingSource,
    log_grounding_cache_diagnostics,
)
from core.agent_harness.grounding.models import CacheStats
from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
from core.agent_harness.prompts.skills.loader import list_action_skills
from core.agent_harness.session import default_session_repo, default_session_store
from core.agent_harness.session.integration_resolution import (
    has_resolved_integrations,
    resolve_integrations,
)
from core.agent_harness.session.terminal_access import (
    background_investigations,
    background_mode_enabled,
    background_notification_channels,
    clear_pending_autosubmit,
    pop_turn_outcome_hint,
    session_terminal,
    set_auto_command,
    set_turn_outcome_hint,
    trust_mode_enabled,
)
from core.agent_harness.session.want_me_to import WANT_ME_TO_MARKER, closer_tail_from
from core.agent_harness.session_goal.goal import (
    MAX_GOAL_CONDITION_CHARS,
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    clear_session_goal,
    session_goal_is_active,
    session_goal_is_attached,
    session_goal_is_paused,
    strip_session_goal_progress_tags,
    strip_shell_prompt_chrome,
)
from core.agent_harness.session_goal.progress import (
    format_session_goal_progress,
    format_session_goal_status_line,
)
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.chat_api import ChatTurnBindings, dispatch_chat_turn
from core.agent_harness.turns.host_cancel import ensure_turn_cancel, host_cancel_requested
from core.agent_harness.turns.orchestrator import stream_answer
from core.agent_harness.turns.turn_results import (
    ToolCallingAccountingStatus,
    ToolCallingTurnResult,
    TurnResult,
)

__all__ = [
    "MAX_GOAL_CONDITION_CHARS",
    "WANT_ME_TO_MARKER",
    "CacheStats",
    "ChatTurnBindings",
    "DefaultErrorReporter",
    "DefaultPromptContextProvider",
    "DefaultRunRecordFactory",
    "DefaultTurnAccounting",
    "GroundingSource",
    "LlmRunInfo",
    "SessionGoal",
    "SessionGoalReason",
    "SessionGoalStatus",
    "ToolCallingAccountingStatus",
    "ToolCallingTurnResult",
    "TurnResult",
    "attach_session_goal",
    "background_investigations",
    "background_mode_enabled",
    "background_notification_channels",
    "clear_pending_autosubmit",
    "clear_session_goal",
    "closer_tail_from",
    "default_session_repo",
    "default_session_store",
    "dispatch_chat_turn",
    "ensure_turn_cancel",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "format_token_total",
    "has_resolved_integrations",
    "host_cancel_requested",
    "list_action_skills",
    "log_grounding_cache_diagnostics",
    "pop_turn_outcome_hint",
    "record_llm_turn",
    "resolve_integrations",
    "run_until_session_goal",
    "session_goal_is_active",
    "session_goal_is_attached",
    "session_goal_is_paused",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "stream_answer",
    "strip_session_goal_progress_tags",
    "strip_shell_prompt_chrome",
    "trust_mode_enabled",
]
