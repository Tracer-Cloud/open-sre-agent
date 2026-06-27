"""Diagnose node — parse investigation conclusions into structured RCA fields.

Node contract:
    Entrypoint : diagnose(state: InvestigationState) -> dict[str, Any]
    Reads      : agent_messages, evidence, alert_name, alert_source,
                 root_cause (idempotency guard — skips if already set)
    Writes     : root_cause, root_cause_category, causal_chain,
                 validated_claims, non_validated_claims, remediation_steps,
                 validity_score, investigation_recommendations, evidence,
                 evidence_entries, agent_messages
"""

from core.domain.diagnosis import InvestigationResult
from tools.investigation.stages.diagnose.node import diagnose, parse_diagnosis

__all__ = ["InvestigationResult", "diagnose", "parse_diagnosis"]
