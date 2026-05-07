"""Shared domain types — decoupled from any single module."""

from app.types.chat import AssistantTurn, BoundChatModel, ToolCallPayload
from app.types.config import Configurable, NodeConfig, get_configurable
from app.types.evidence import EvidenceSource
from app.types.retrieval import (
    AggregationSpec,
    FieldSelection,
    FilterCondition,
    RetrievalControls,
    RetrievalControlsMap,
    RetrievalIntent,
    TimeBounds,
)
from app.types.tools import ToolSurface

__all__ = [
    "AssistantTurn",
    "BoundChatModel",
    "Configurable",
    "EvidenceSource",
    "NodeConfig",
    "ToolSurface",
    "RetrievalIntent",
    "RetrievalControls",
    "RetrievalControlsMap",
    "TimeBounds",
    "FilterCondition",
    "FieldSelection",
    "AggregationSpec",
    "get_configurable",
    "ToolCallPayload",
]
