"""Plan actions stage — prioritized investigation tool selection.

Node contract:
    Entrypoint : plan_actions(state: InvestigationState) -> dict[str, Any]
    Reads      : resolved_integrations, alert_name, alert_source, raw_alert,
                 retrieval_controls, is_noise
    Writes     : planned_actions, plan_rationale, retrieval_controls, plan_audit,
                 connected_tool_context (for investigate stage)
"""

from app.core.orchestration.node.plan_actions.node import plan_actions

__all__ = ["plan_actions"]
