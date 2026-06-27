"""Chat-mode slice for :class:`~context.state.models.AgentState`.

Investigation pipeline slices live in :mod:`context.state.runtime_slices`.
"""

from __future__ import annotations

from typing_extensions import TypedDict

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


class ChatStateSlice(TypedDict, total=False):
    """Conversation history for chat mode."""

    messages: list


__all__ = [
    "AlertInputSlice",
    "ChatStateSlice",
    "DeliveryContextSlice",
    "DeliveryOutputSlice",
    "DiagnosisSlice",
    "EvalHarnessSlice",
    "InvestigationPlanSlice",
    "InvestigationRuntimeSlice",
    "MaskingSlice",
    "SessionContext",
]
