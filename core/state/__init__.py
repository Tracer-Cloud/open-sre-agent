"""Shared agent state: investigation pipeline state and the per-session store.

Holds the immutable investigation state (``AgentState`` and its slices) and the
mutable per-session agent store reached through ``session.agent``.
"""

from __future__ import annotations

from core.state.agent_state import (
    MAX_CONVERSATION_MESSAGES,
    MAX_CONVERSATION_TURNS,
    AgentMessageRole,
    AgentModelInfo,
    AgentRunStatus,
    AgentStateChange,
    AgentStateError,
    MutableAgentState,
    SessionAgentSnapshot,
    TurnRuntimeInput,
    create_mutable_agent_state,
)
from core.state.evidence import EvidenceEntry
from core.state.models import (
    AgentState,
    AgentStateModel,
    InvestigationState,
    make_chat_state,
    model_default_payload,
)
from core.state.runtime_slices import (
    AlertInputSlice,
    CallerMetadataSlice,
    DeliveryContextSlice,
    DeliveryOutputSlice,
    DiagnosisSlice,
    EvalHarnessSlice,
    InvestigationPlanSlice,
    InvestigationRuntimeSlice,
    MaskingSlice,
)
from core.state.slices import ChatStateSlice
from core.state.types import AgentMode, ChatMessage, ChatMessageModel
from core.state.updates import apply_state_updates

__all__ = [
    "TurnRuntimeInput",
    "AgentMessageRole",
    "AgentModelInfo",
    "AgentRunStatus",
    "AgentStateChange",
    "AgentStateError",
    "SessionAgentSnapshot",
    "MAX_CONVERSATION_MESSAGES",
    "MAX_CONVERSATION_TURNS",
    "MutableAgentState",
    "create_mutable_agent_state",
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
    "InvestigationRuntimeSlice",
    "InvestigationState",
    "MaskingSlice",
    "CallerMetadataSlice",
    "apply_state_updates",
    "make_chat_state",
    "model_default_payload",
]
