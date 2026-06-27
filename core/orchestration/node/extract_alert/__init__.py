"""Extract alert node — classify and parse raw alerts into structured state.

Node contract:
    Entrypoint : extract_alert(state: InvestigationState) -> dict[str, Any]
    Reads      : raw_alert, slack_context, org_id
    Writes     : alert_name, pipeline_name, severity, problem_md,
                 alert_source, raw_alert (enriched), is_noise
"""

from core.orchestration.node.extract_alert.node import extract_alert

__all__ = ["extract_alert"]
