"""AgentState TypedDict and its Pydantic validator model.

``AgentStateModel`` is the single source of truth for field defaults and
validation. ``AgentState`` composes investigation slices from
:mod:`context.state.runtime_slices` and chat slice from
:mod:`context.state.slices`; the runtime dict remains flat.

Whenever you add or remove a field, update ``AgentStateModel`` and the
appropriate slice in ``runtime_slices.py`` or ``slices.py``.
``tests/app/test_agent_state_sync.py`` asserts slice keys and Pydantic fields
stay aligned with ``AgentState``.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import ConfigDict, Field

from config.strict_config import StrictConfigModel
from context.state.runtime_slices import (
    AlertInputSlice,
    DeliveryContextSlice,
    DeliveryOutputSlice,
    DiagnosisSlice,
    EvalHarnessSlice,
    InvestigationPlanSlice,
    InvestigationRuntimeSlice,
    MaskingSlice,
    SessionContext,
)
from context.state.slices import ChatStateSlice
from context.state.types import AgentMode, ChatMessage, ChatMessageModel
from core.domain.types.retrieval import RetrievalControlsMap


class AgentState(
    SessionContext,
    ChatStateSlice,
    AlertInputSlice,
    InvestigationPlanSlice,
    InvestigationRuntimeSlice,
    DiagnosisSlice,
    MaskingSlice,
    DeliveryContextSlice,
    DeliveryOutputSlice,
    EvalHarnessSlice,
    total=False,
):
    """Unified flat state for chat and investigation modes.

    Chat mode primarily uses ``ChatStateSlice`` + ``SessionContext``.
    Investigation mode uses alert, plan, runtime, diagnosis, and delivery slices.
    See :mod:`context.state.runtime_slices` for investigation field groupings.
    """


InvestigationState = AgentState


class AgentStateModel(StrictConfigModel):
    """Runtime-validated state envelope used by state constructors."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=(), populate_by_name=True)

    mode: AgentMode = "chat"
    route: str = ""
    org_id: str = ""
    user_id: str = ""
    user_email: str = ""
    user_name: str = ""
    organization_slug: str = ""
    messages: list[ChatMessageModel] = Field(default_factory=list)
    is_noise: bool = False
    alert_name: str = ""
    pipeline_name: str = ""
    severity: str = ""
    alert_source: str = ""
    raw_alert: str | dict[str, Any] = Field(default_factory=lambda: {})
    alert_json: dict[str, Any] = Field(default_factory=dict)
    planned_actions: list[str] = Field(default_factory=list)
    plan_rationale: str = ""
    retrieval_controls: RetrievalControlsMap | None = None
    available_sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    available_action_names: list[str] = Field(default_factory=list)
    tool_budget: int = Field(
        default=10, ge=1, le=50, description="Maximum tools to select per step"
    )
    plan_audit: dict[str, Any] = Field(
        default_factory=dict, description="Audit trail for planning step"
    )
    resolved_integrations: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    correlation: dict[str, Any] = Field(default_factory=dict)
    root_cause: str = ""
    root_cause_category: str = ""
    validated_claims: list[dict[str, Any]] = Field(default_factory=list)
    non_validated_claims: list[dict[str, Any]] = Field(default_factory=list)
    validity_score: float = 0.0
    investigation_recommendations: list[str] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)
    investigation_loop_count: int = 0
    hypotheses: list[str] = Field(default_factory=list)
    executed_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    evidence_entries: list[dict[str, Any]] = Field(default_factory=list)
    hypothesis_results: list[dict[str, Any]] = Field(default_factory=list)
    action_to_run: str = ""
    investigation_started_at: float = 0.0
    incident_window: dict[str, Any] | None = None
    incident_window_history: list[dict[str, Any]] | None = None
    masking_map: dict[str, str] = Field(default_factory=dict)
    slack_context: dict[str, Any] = Field(default_factory=dict)
    discord_context: dict[str, Any] = Field(default_factory=dict)
    telegram_context: dict[str, Any] = Field(default_factory=dict)
    whatsapp_context: dict[str, Any] = Field(default_factory=dict)
    twilio_sms_context: dict[str, Any] = Field(default_factory=dict)
    openclaw_context: dict[str, Any] = Field(default_factory=dict)
    thread_id: str = ""
    run_id: str = ""
    auth_token: str = Field(default="", alias="_auth_token", exclude=True)
    slack_message: str = ""
    problem_md: str = ""
    summary: str = ""
    problem_report: dict[str, Any] = Field(default_factory=dict)
    report: str = ""
    opensre_evaluate: bool = False
    opensre_eval_rubric: str = ""
    opensre_llm_eval: dict[str, Any] = Field(default_factory=dict)


def model_default_payload(*exclude: str) -> dict[str, Any]:
    """Return default field values from ``AgentStateModel``, omitting ``exclude`` keys."""
    skip = frozenset(exclude)
    model = AgentStateModel()
    dumped = model.model_dump(mode="python", by_alias=True, exclude_none=True)
    return {key: value for key, value in dumped.items() if key not in skip}


def make_chat_state(
    org_id: str = "",
    user_id: str = "",
    user_email: str = "",
    user_name: str = "",
    organization_slug: str = "",
    messages: list[ChatMessage] | None = None,
) -> AgentState:
    """Create initial state for chat mode."""
    state = AgentStateModel.model_validate(
        {
            "mode": "chat",
            "org_id": org_id,
            "user_id": user_id,
            "user_email": user_email,
            "user_name": user_name,
            "organization_slug": organization_slug,
            "messages": messages or [],
            "context": {},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))


__all__ = [
    "AgentState",
    "AgentStateModel",
    "InvestigationState",
    "make_chat_state",
    "model_default_payload",
]
