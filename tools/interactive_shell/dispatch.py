"""Slash-dispatch and provider-switch presenter port for interactive-shell action tools.

Mirrors :mod:`tools.interactive_shell.subprocess` — action tools depend only on
this Protocol, and the owning surface (interactive REPL, gateway) injects a
concrete implementation onto :class:`ActionToolContext.action_dispatch_presenter`
at composition-root time. This keeps ``tools/interactive_shell/actions`` free of
direct ``surfaces.interactive_shell`` imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.agent_harness.tools.tool_context import ActionToolContext
from tools.interactive_shell.shared import ExecutionPolicyResult


@runtime_checkable
class ActionDispatchPresenter(Protocol):
    """Surface-injected slash-dispatch, execution-confirm, and provider-switch hooks."""

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        """Apply execution policy UX and return whether the action may proceed."""

    def slash_command_known(self, name: str) -> bool:
        """True when ``name`` (e.g. ``/model``) is a registered slash command."""

    def dispatch_slash(self, command: str, *, policy_precleared: bool = False) -> bool:
        """Run a slash command; return whether the calling turn should continue."""

    def repl_tty_interactive(self) -> bool:
        """True when the running surface is an interactive TTY REPL."""

    def queue_slash_for_exclusive_dispatch(self, command: str) -> None:
        """Notify the user and hand ``command`` back to the REPL loop to dispatch."""

    def format_turn_outcome(self, command: str, *, kind: str, ok: bool) -> str:
        """Render a terminal turn-outcome string for a declined/blocked action."""

    def switch_llm_provider(self, target: str) -> bool:
        """Switch the active LLM provider; return whether it succeeded."""

    def switch_reasoning_model(self, target: str) -> bool:
        """Switch the active reasoning model; return whether it succeeded."""


def require_action_dispatch_presenter(ctx: ActionToolContext) -> ActionDispatchPresenter:
    presenter = ctx.action_dispatch_presenter
    if not isinstance(presenter, ActionDispatchPresenter):
        raise RuntimeError("action dispatch presenter is required for this action tool")
    return presenter


__all__ = ["ActionDispatchPresenter", "require_action_dispatch_presenter"]
