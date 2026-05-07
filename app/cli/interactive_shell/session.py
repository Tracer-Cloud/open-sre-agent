"""In-memory session state that persists across REPL turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.cli.interactive_shell.tasks import TaskRegistry


@dataclass
class TerminalMetricsSnapshot:
    """Session-level aggregate counters for interactive-shell analytics."""

    turn_index: int
    fallback_count: int
    action_success_percent: float
    fallback_rate_percent: float


@dataclass
class ReplSession:
    """Per-REPL-process accumulated state.

    Carries everything we want to persist across individual investigations
    within the same REPL session: previous investigation state (for follow-up
    questions), accumulated infra context (service names, clusters observed),
    trust mode flag, and a short interaction history for /status.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    """Each entry has type, text, and ok fields for shell, slash, alert, and chat turns."""

    last_state: dict[str, Any] | None = None
    """The final AgentState from the most recent investigation, used by follow-ups."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Reusable infra context — service names, clusters, regions — learned from
    earlier investigations that should seed future ones."""

    trust_mode: bool = False
    """When True, confirmation prompts for elevated REPL actions are skipped."""

    token_usage: dict[str, int] = field(default_factory=dict)
    """Accumulated token counts: {"input": N, "output": N}. Populated when available."""

    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    """LangGraph-free terminal assistant history: alternating (\"user\"|\"assistant\", text)."""

    task_registry: TaskRegistry = field(default_factory=TaskRegistry)
    """Recent in-flight and completed shell tasks for /tasks and /cancel."""

    history_generation: int = 0
    """Incremented on /reset so background synthetic watchers can skip stale history writes."""

    terminal_turn_count: int = 0
    terminal_fallback_count: int = 0
    terminal_actions_executed_count: int = 0
    terminal_actions_success_count: int = 0

    # Keys from a completed AgentState that carry reusable infra context into
    # the next investigation.  Kept as a class-level tuple so any caller that
    # wants to know "what counts as accumulated context" has a single source.
    _ACCUMULATED_KEYS: tuple[str, ...] = (
        "service",
        "pipeline_name",
        "cluster_name",
        "region",
        "environment",
    )

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        """Append an entry to the session history."""
        self.history.append({"type": kind, "text": text, "ok": ok})

    def mark_latest(self, *, ok: bool, kind: str | None = None) -> None:
        """Update the latest history entry, optionally scanning for a matching kind."""
        for latest in reversed(self.history):
            if kind is not None and latest.get("type") != kind:
                continue
            latest["ok"] = ok
            return

    def accumulate_from_state(self, state: dict[str, Any] | None) -> None:
        """Extract reusable infra hints from a completed investigation state.

        Called after every successful investigation (whether triggered by
        free-text input or by the ``/investigate`` slash command) so that
        subsequent investigations within the same REPL session inherit the
        service / cluster / region context discovered earlier.
        """
        if not state:
            return
        for key in self._ACCUMULATED_KEYS:
            value = state.get(key)
            if value:
                self.accumulated_context[key] = value

    def clear(self) -> None:
        """Reset the session to a fresh state (used by /reset)."""
        self.history_generation += 1
        self.history.clear()
        self.last_state = None
        self.accumulated_context.clear()
        self.token_usage.clear()
        self.cli_agent_messages.clear()
        self.task_registry = TaskRegistry()

        self.terminal_turn_count = 0
        self.terminal_fallback_count = 0
        self.terminal_actions_executed_count = 0
        self.terminal_actions_success_count = 0
        # trust_mode is intentionally preserved across /reset

    def record_terminal_turn(
        self,
        *,
        executed_count: int,
        executed_success_count: int,
        fallback_to_llm: bool,
    ) -> TerminalMetricsSnapshot:
        """Update aggregate terminal metrics and return a stable snapshot."""
        self.terminal_turn_count += 1
        self.terminal_actions_executed_count += max(0, executed_count)
        self.terminal_actions_success_count += max(0, executed_success_count)
        if fallback_to_llm:
            self.terminal_fallback_count += 1
        action_success_percent = (
            100.0 * self.terminal_actions_success_count / self.terminal_actions_executed_count
            if self.terminal_actions_executed_count > 0
            else 0.0
        )
        fallback_rate_percent = 100.0 * self.terminal_fallback_count / self.terminal_turn_count
        return TerminalMetricsSnapshot(
            turn_index=self.terminal_turn_count,
            fallback_count=self.terminal_fallback_count,
            action_success_percent=action_success_percent,
            fallback_rate_percent=fallback_rate_percent,
        )
