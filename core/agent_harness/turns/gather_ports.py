"""How a surface runs and observes the evidence-gathering phase.

A leaf module so both the agent and the surfaces that configure it can import
the type without reaching through the turn machinery.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agent_harness.ports import ToolEventObserver
from core.agent_harness.turns.evidence_driver import PersistToolCalls


@dataclass(frozen=True, slots=True)
class GatherPorts:
    """What a surface plugs into the gather phase.

    Grouped rather than passed as four keywords because they vary together: a
    REPL streams progress and persists tool calls, a scheduled report does
    neither and only raises the iteration budget.

    Frozen: gateway agents are pooled per session and rebound per turn, so a
    mutable ports object would let one turn retarget another's progress stream.
    """

    #: Run the phase at all. A turn with no tools to gather from sets this False.
    enabled: bool = True
    #: Loop budget. ``None`` takes the driver's default.
    max_iterations: int | None = None
    #: Called per tool event — the REPL prints a live line per tool call.
    on_progress: ToolEventObserver | None = None
    #: Called with the executed calls once the phase ends, for session history.
    persist: PersistToolCalls | None = None


#: A phase that does not run. Named so callers read as intent, not a bare flag.
GATHER_DISABLED = GatherPorts(enabled=False)


__all__ = ["GATHER_DISABLED", "GatherPorts"]
