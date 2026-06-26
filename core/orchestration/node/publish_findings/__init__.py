"""Publish findings node — format and deliver investigation reports.

Node contract:
    Entrypoint : deliver(state: InvestigationState) -> dict[str, Any]
    Reads      : root_cause, validated_claims, non_validated_claims,
                 remediation_steps, correlation, evidence, resolved_integrations,
                 slack_context, telegram_context, whatsapp_context,
                 discord_context, problem_md, masking_context, opensre_evaluate
    Writes     : slack_message, report, opensre_llm_eval (optional)
"""

from core.orchestration.node.publish_findings.node import deliver, generate_report

__all__ = [
    "deliver",
    "generate_report",
]
