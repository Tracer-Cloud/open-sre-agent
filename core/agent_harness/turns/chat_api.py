"""Internal chat seam — one entry over the shared ``run_turn`` engine.

The **public** host API is :class:`~core.agent_harness.harness.AgentSession`
(``chat`` / ``investigate``). Adapters (shell TTY dispatcher, ``HeadlessAgent``)
build :class:`ChatTurnBindings` from their surface ports, then call
:func:`dispatch_chat_turn` from inside ``.dispatch``. This module must not
import ``surfaces`` or ``gateway``. Investigation stays on Path 2 via
:meth:`AgentSession.investigate` (installed payload runner).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agent_harness.ports import (
    ConfirmFn,
    EvidenceGatherer,
    ExecuteActions,
    OutputSink,
    SessionStore,
    StreamAnswerFn,
    TurnAccounting,
)
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import TurnResult


@dataclass(frozen=True, slots=True)
class ChatTurnBindings:
    """Ports a host binds before one chat turn.

    Strategies are already surface-bound (session / sink / tools). This type
    is the host contract — not a second engine.
    """

    execute_actions: ExecuteActions
    answer: StreamAnswerFn
    gather: EvidenceGatherer
    accounting: TurnAccounting
    confirm_fn: ConfirmFn | None = None
    is_tty: bool | None = None
    surface: str = "interactive_shell"
    output: OutputSink | None = None


def dispatch_chat_turn(
    message: str,
    session: SessionStore,
    bindings: ChatTurnBindings,
) -> TurnResult:
    """Run one chat turn. Thin facade over :func:`run_turn`.

    Every chat host should call this after building :class:`ChatTurnBindings`.
    Do not add new top-level entrypoints that call ``run_turn`` directly.
    """
    return run_turn(
        message,
        session,
        execute_actions=bindings.execute_actions,
        answer=bindings.answer,
        gather=bindings.gather,
        accounting=bindings.accounting,
        confirm_fn=bindings.confirm_fn,
        is_tty=bindings.is_tty,
        surface=bindings.surface,
        output=bindings.output,
    )


__all__ = ["ChatTurnBindings", "dispatch_chat_turn"]
