"""Turn-result data model and the single owner of shell-turn accounting.

This module holds the shell's accounting side effects around the core
"facts only" turn-result models: action-agent analytics, terminal-turn
aggregate telemetry, prompt-recorder flushing, conversational-turn
persistence, and the final assistant-intent stamp.
"""

from __future__ import annotations

from dataclasses import dataclass

# The neutral "facts only" turn-result models live in the decoupled agent
# package; this module owns only the shell's accounting side effects over them.
from core.agent_harness.models.turn_results import (
    ShellTurnResult,
    ToolCallingAccountingStatus,
    ToolCallingTurnResult,
)
from core.agent_harness.session import Session
from platform.analytics.cli import capture_terminal_turn_summarized
from surfaces.interactive_shell.utils.telemetry import PromptRecorder


@dataclass
class ShellTurnAccounting:
    """Single owner of a shell turn's accounting side effects.

    Separates "what happened" (decided by the turn flow) from "how it is
    accounted for": action-agent analytics, terminal-turn aggregate telemetry,
    prompt-recorder flushing, conversational-turn persistence, and the final
    assistant-intent stamp.
    """

    session: Session
    text: str
    recorder: PromptRecorder | None

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        """Emit action-agent analytics and update terminal-turn aggregates."""
        self._record_action_analytics(action_result)
        self._record_terminal_turn(action_result)

    def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
        """Flush the recorder, persist the turn, and stamp the session intent."""
        self._flush_prompt_recorder(result)
        if result.llm_run is not None:
            self.session.record("cli_agent", self.text)
        self.session.last_assistant_intent = result.final_intent
        return result

    def _record_action_analytics(self, action_result: ToolCallingTurnResult) -> None:
        from platform.analytics.cli import (
            capture_repl_execution_policy_decision,
            capture_terminal_actions_executed,
            capture_terminal_actions_planned,
        )

        if action_result.accounting_status == "not_run":
            capture_terminal_actions_executed(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
            )
            return

        capture_terminal_actions_planned(
            planned_count=action_result.planned_count,
            has_unhandled_clause=action_result.has_unhandled_clause,
        )
        capture_repl_execution_policy_decision(
            {
                "policy_stage": "shell_action_agent",
                "policy_trace": (
                    "agent_tool_calls" if action_result.planned_count else "assistant_handoff"
                ),
                "planned_count": action_result.planned_count,
                "has_unhandled_clause": action_result.has_unhandled_clause,
            }
        )
        capture_terminal_actions_executed(
            planned_count=action_result.planned_count,
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
        )

    def _record_terminal_turn(self, action_result: ToolCallingTurnResult) -> None:
        fallback_to_llm = not action_result.handled
        snapshot = self.session.metrics.record_turn(
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
            fallback_to_llm=fallback_to_llm,
        )
        capture_terminal_turn_summarized(
            planned_count=action_result.planned_count,
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
            fallback_to_llm=fallback_to_llm,
            session_turn_index=snapshot.turn_index,
            session_fallback_count=snapshot.fallback_count,
            session_action_success_percent=snapshot.action_success_percent,
            session_fallback_rate_percent=snapshot.fallback_rate_percent,
        )

    def _flush_prompt_recorder(self, result: ShellTurnResult) -> None:
        # Pending turn LLM/error state is consumed unconditionally so a turn
        # that stages it can never leak it into a later turn's flush.
        pending_run = self.session.pop_pending_turn_llm()
        pending_error = self.session.pop_pending_turn_error()
        if self.recorder is None:
            return
        if pending_error is not None:
            self.recorder.set_error(pending_error[0], pending_error[1])
        self.recorder.set_response(
            result.assistant_response_text,
            result.llm_run if result.llm_run is not None else pending_run,
        )
        self.recorder.flush()


__all__ = [
    "ShellTurnAccounting",
    "ShellTurnResult",
    "ToolCallingAccountingStatus",
    "ToolCallingTurnResult",
]
