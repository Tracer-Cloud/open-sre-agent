"""How a surface runs and observes the evidence-gathering phase.

A leaf module so both the agent and the surfaces that configure it can import
the type without reaching through the turn machinery.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.agent_harness.ports import ToolEventObserver

#: Persists gathered tool calls as ``[(tool_name, tool_input, output), ...]``.
PersistToolCalls = Callable[[list[tuple[str, Mapping[str, object], Any]]], None]

#: Loop budget for headless metric reports (PostHog / digests): schema discovery plus
#: one query per metric needs more than the chat default; passed as ``max_iterations``.
MAX_REPORT_GATHER_ITERATIONS = 12


@dataclass(frozen=True, slots=True)
class GatherPhase:
    """What a surface plugs into the gather phase.

    Grouped rather than passed as four keywords because they vary together: a
    REPL streams progress and persists tool calls, a scheduled report does
    neither and only raises the iteration budget.

    Frozen: gateway agents are pooled per session and rebound per turn, so a
    mutable gather object would let one turn retarget another's progress stream.
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
GATHER_DISABLED = GatherPhase(enabled=False)


__all__ = ["GATHER_DISABLED", "MAX_REPORT_GATHER_ITERATIONS", "GatherPhase", "PersistToolCalls"]
