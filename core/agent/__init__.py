"""The reusable tool-calling agent, one file per responsibility.

- ``agent.py``          — ``Agent``: the object surfaces create and call.
- ``react_loop.py``     — the think -> call-tools -> observe loop it runs.
- ``loop_host.py``      — the callbacks the loop needs from whoever drives it.
- ``run_io.py``         — the input the loop takes and the result it returns.
- ``mixins.py``         — the reusable behaviors ``Agent`` is built from.
- ``provider_hooks.py`` — optional hooks applied around each LLM call.

``from core.agent import Agent`` is the entry point. See
``core/agent_harness/AGENTS.md`` for how surfaces build and drive an ``Agent``.
"""

from __future__ import annotations

from core.agent.agent import Agent
from core.agent.run_io import AgentRunResult

__all__ = ["Agent", "AgentRunResult"]
