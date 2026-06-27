"""Investigate node — connected ReAct investigation agent.

Node contract:
    Entrypoint : ConnectedInvestigationAgent().run(state, on_event=None) -> dict[str, Any]
    Reads      : planned_actions, resolved_integrations, retrieval_controls,
                 agent_messages, alert_name, raw_alert
    Writes     : evidence, agent_messages, executed_hypotheses,
                 investigation_started_at, investigation_loop_count
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.investigation.stages.gather_evidence.agent import (
        ConnectedInvestigationAgent,
        InvestigationAgent,
        get_investigation_agent_class,
    )

__all__ = ["ConnectedInvestigationAgent", "InvestigationAgent", "get_investigation_agent_class"]


def __getattr__(name: str) -> object:
    if name in __all__:
        from tools.investigation.stages.gather_evidence.agent import (
            ConnectedInvestigationAgent,
            InvestigationAgent,
            get_investigation_agent_class,
        )

        return {
            "ConnectedInvestigationAgent": ConnectedInvestigationAgent,
            "InvestigationAgent": InvestigationAgent,
            "get_investigation_agent_class": get_investigation_agent_class,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
