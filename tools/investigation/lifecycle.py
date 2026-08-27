"""Deprecated facade for the connected investigation pipeline.

Prefer :func:`tools.investigation.agent_pipeline.run_agent_investigation`.
This module remains for import compatibility; it delegates to the agent-first
runner and emits :class:`DeprecationWarning`.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from core.state import AgentState

if TYPE_CHECKING:
    from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent

_DEPRECATION_MESSAGE = (
    "tools.investigation.lifecycle.run_connected_investigation is deprecated; "
    "use tools.investigation.agent_pipeline.run_agent_investigation instead. "
    "The lifecycle module remains as a compatibility facade and will be removed "
    "in a later release."
)


def run_connected_investigation(
    state: AgentState,
    *,
    agent_class: type[ConnectedInvestigationAgent] | None = None,
) -> AgentState:
    """Deprecated: resolve → intake → plan → gather → diagnose → deliver.

    Delegates to :func:`tools.investigation.agent_pipeline.run_agent_investigation`,
    which keeps the same stage functions and runs gather via the native
    ``core.agent.Agent`` ReAct loop.
    """
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
    from tools.investigation.agent_pipeline import run_agent_investigation

    return run_agent_investigation(state, agent_class=agent_class)
