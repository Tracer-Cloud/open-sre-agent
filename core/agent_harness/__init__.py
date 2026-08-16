"""Agent harness — public API for embedders.

The entry point (:class:`AgentSession`), its config, the session object and
manager, and the sink Protocol a host implements. Hosts also use
:mod:`core.agent_harness.spi` (what a host consumes to integrate a turn) and
:mod:`core.agent_harness.runtime` (build and run the agent).

Nothing under ``agent_harness/`` may import from ``interactive_shell``,
``tools``, or ``integrations``; the dependency direction is
``interactive_shell -> agent_harness -> core``.
"""

from __future__ import annotations

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.ports import OutputSink
from core.agent_harness.session import SessionCore, SessionManager

__all__ = [
    "AgentSession",
    "OutputSink",
    "SessionConfig",
    "SessionCore",
    "SessionManager",
]
