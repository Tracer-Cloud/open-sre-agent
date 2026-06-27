"""Shared agent state for runtime request assembly.

Owns the mutable per-session store and immutable read models used to assemble
runtime requests without exposing live mutable internals.
"""

from __future__ import annotations

from context.state.agent_state import (
    AgentContextInput,
    AgentMessageRole,
    AgentModelInfo,
    AgentRunStatus,
    AgentStateChange,
    AgentStateError,
    AgentStateSnapshot,
    MAX_CONVERSATION_MESSAGES,
    MAX_CONVERSATION_TURNS,
    MutableAgentState,
    create_mutable_agent_state,
)
from context.state.evidence import EvidenceEntry
from context.state.models import (
    AgentState,
    AgentStateModel,
    InvestigationState,
    make_chat_state,
    model_default_payload,
)
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
from context.state.updates import apply_state_updates

__all__ = [
    "AgentContextInput",
    "AgentMessageRole",
    "AgentModelInfo",
    "AgentRunStatus",
    "AgentStateChange",
    "AgentStateError",
    "AgentStateSnapshot",
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
    "SessionContext",
    "apply_state_updates",
    "make_chat_state",
    "model_default_payload",
]
