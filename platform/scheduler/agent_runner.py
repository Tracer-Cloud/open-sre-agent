"""Headless agent-runner seam for scheduled-delivery tasks.

Some scheduled reports (e.g. the Sentry morning digest) run a single
headless agent turn with skill guidance instead of the investigation pipeline.
``platform.scheduler`` must not import ``tools`` or ``integrations`` directly
(T-4 layering audit, issue #3352), so this module mirrors
:mod:`platform.scheduler.investigation_runner`: the scheduler calls
:func:`invoke_agent_runner`, and a higher-layer bootstrap registers the
concrete implementation.
"""

from __future__ import annotations

from typing import Any, Protocol

AgentPayload = dict[str, Any]


class AgentRunner(Protocol):
    """Callable that runs a headless agent turn and returns report text."""

    def __call__(self, payload: AgentPayload) -> str:
        """Run the agent for ``payload`` and return the formatted report."""


class AgentRunnerNotRegisteredError(RuntimeError):
    """Raised when the scheduler executes a task before a runner is registered."""


_runner: AgentRunner | None = None


def register_agent_runner(runner: AgentRunner | None) -> None:
    """Bind (or clear) the concrete agent runner used by the scheduler."""
    global _runner
    _runner = runner


def get_agent_runner() -> AgentRunner | None:
    """Return the currently registered runner, if any."""
    return _runner


def invoke_agent_runner(payload: AgentPayload) -> str:
    """Invoke the currently registered agent runner.

    Raises :class:`AgentRunnerNotRegisteredError` when no runner has been
    registered.
    """
    if _runner is None:
        raise AgentRunnerNotRegisteredError(
            "Scheduler has no agent runner registered. Call "
            "tools.sentry.scheduler_bootstrap.install() at startup "
            "(the `opensre sentry digest` command does this automatically)."
        )
    return _runner(payload)


__all__ = [
    "AgentPayload",
    "AgentRunner",
    "AgentRunnerNotRegisteredError",
    "get_agent_runner",
    "invoke_agent_runner",
    "register_agent_runner",
]
