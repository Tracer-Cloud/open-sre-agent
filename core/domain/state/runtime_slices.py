"""Investigation pipeline slice TypedDicts owned by orchestration stages.

The pipeline still uses one flat runtime dict (:class:`~core.domain.state.AgentState`).
These TypedDicts document which fields belong together and which stages typically own them.

Stage ownership (typical read/write):

- ``resolve_integrations`` → ``InvestigationRuntimeSlice.resolved_integrations``
- ``extract_alert`` → ``AlertInputSlice``
- ``plan_actions`` → ``InvestigationPlanSlice``
- ``investigate`` → ``InvestigationRuntimeSlice`` (evidence, hypotheses, …)
- ``diagnose`` → ``DiagnosisSlice``
- ``deliver`` → ``DeliveryOutputSlice`` (+ reads diagnosis/runtime slices)
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict

from core.domain.state.types import AgentMode
from core.domain.types.retrieval import RetrievalControlsMap


class SessionContext(TypedDict, total=False):
    """Mode, auth, and run identifiers injected by callers."""

    mode: AgentMode
    route: str
    org_id: str
    user_id: str
    user_email: str
    user_name: str
    organization_slug: str
    thread_id: str
    run_id: str
    _auth_token: str


class AlertInputSlice(TypedDict, total=False):
    """Raw alert input and incident time window (``extract_alert``)."""

    is_noise: bool
    alert_name: str
    pipeline_name: str
    severity: str
    alert_source: str
    raw_alert: str | dict[str, Any]
    alert_json: dict[str, Any]
    incident_window: dict[str, Any] | None
    incident_window_history: list[dict[str, Any]] | None


class InvestigationPlanSlice(TypedDict, total=False):
    """Tool plan produced by ``plan_actions``."""

    planned_actions: list[str]
    plan_rationale: str
    retrieval_controls: RetrievalControlsMap | None
    available_sources: dict[str, dict]
    available_action_names: list[str]
    tool_budget: int
    plan_audit: dict[str, Any]


class InvestigationRuntimeSlice(TypedDict, total=False):
    """Integrations, collected evidence, and investigate-loop metadata."""

    resolved_integrations: dict[str, Any]
    context: dict[str, Any]
    evidence: dict[str, Any]
    correlation: dict[str, Any]
    investigation_loop_count: int
    hypotheses: list[str]
    executed_hypotheses: list[dict[str, Any]]
    evidence_entries: list[dict[str, Any]]
    hypothesis_results: list[dict[str, Any]]
    action_to_run: str
    investigation_started_at: float


class DiagnosisSlice(TypedDict, total=False):
    """Structured RCA output from ``diagnose``."""

    root_cause: str
    root_cause_category: str
    validated_claims: list[dict[str, Any]]
    non_validated_claims: list[dict[str, Any]]
    validity_score: float
    investigation_recommendations: list[str]
    remediation_steps: list[str]


class MaskingSlice(TypedDict, total=False):
    """Reversible infrastructure identifier masking."""

    masking_map: dict[str, str]


class DeliveryContextSlice(TypedDict, total=False):
    """Channel-specific delivery metadata from the triggering surface."""

    slack_context: dict[str, Any]
    discord_context: dict[str, Any]
    telegram_context: dict[str, Any]
    whatsapp_context: dict[str, Any]
    twilio_sms_context: dict[str, Any]
    openclaw_context: dict[str, Any]


class DeliveryOutputSlice(TypedDict, total=False):
    """Rendered report artifacts from ``deliver``."""

    slack_message: str
    problem_md: str
    summary: str
    problem_report: dict[str, Any]
    report: str


class EvalHarnessSlice(TypedDict, total=False):
    """OpenRCA offline evaluation harness fields."""

    opensre_evaluate: bool
    opensre_eval_rubric: str
    opensre_llm_eval: dict[str, Any]


__all__ = [
    "AlertInputSlice",
    "DeliveryContextSlice",
    "DeliveryOutputSlice",
    "DiagnosisSlice",
    "EvalHarnessSlice",
    "InvestigationPlanSlice",
    "InvestigationRuntimeSlice",
    "MaskingSlice",
    "SessionContext",
]
