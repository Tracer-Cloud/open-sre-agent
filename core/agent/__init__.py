"""The stateful ReAct agent primitive: facade, loop, mixins, and hooks.

``agent.py`` holds ``Agent``, the facade that wires per-run context into
``loop.py``'s ``run_react_loop``. ``mixins.py`` holds the reusable behaviors
(event dispatch, tool filtering, steering); ``hooks.py`` holds the provider-hook
delegate. See ``core/agent_harness/AGENTS.md`` for how surfaces build and drive
an ``Agent``.
"""

from __future__ import annotations

from core.agent.agent import Agent
from core.agent.run_io import AgentRunResult

__all__ = ["Agent", "AgentRunResult"]
