"""Agent and investigation state contracts owned by ``core``."""

from core.domain.state.agent_state import (
    AgentState,
    AgentStateModel,
    InvestigationState,
    model_default_payload,
)
from core.domain.state.diagnosis import InvestigationResult, result_to_state
from core.domain.state.evidence import EvidenceEntry
from core.domain.state.factory import make_agent_incident_state, make_chat_state, make_initial_state
from core.domain.state.runtime_slices import (
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
from core.domain.state.slices import ChatStateSlice
from core.domain.state.types import AgentMode, ChatMessage, ChatMessageModel

__all__ = [
    "AgentMode",
    "AgentState",
    "AgentStateModel",
    "AlertInputSlice",
    "ChatMessage",
    "ChatMessageModel",
    "ChatStateSlice",
    "DeliveryContextSlice",
    "DeliveryOutputSlice",
    "DiagnosisSlice",
    "EvalHarnessSlice",
    "EvidenceEntry",
    "InvestigationPlanSlice",
    "InvestigationResult",
    "InvestigationRuntimeSlice",
    "InvestigationState",
    "MaskingSlice",
    "SessionContext",
    "make_agent_incident_state",
    "make_chat_state",
    "make_initial_state",
    "model_default_payload",
    "result_to_state",
]
