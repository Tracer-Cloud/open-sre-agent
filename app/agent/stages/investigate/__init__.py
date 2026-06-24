"""Investigate node — connected ReAct investigation agent.

Node contract:
    Entrypoint : ConnectedInvestigationAgent().run(state, on_event=None) -> dict[str, Any]
    Reads      : planned_actions, resolved_integrations, retrieval_controls,
                 agent_messages, alert_name, raw_alert
    Writes     : evidence, agent_messages, executed_hypotheses,
                 investigation_started_at, investigation_loop_count
"""

from app.agent.stages.investigate.agent import ConnectedInvestigationAgent, InvestigationAgent

__all__ = ["ConnectedInvestigationAgent", "InvestigationAgent"]
