"""Shared domain types — decoupled from any single module."""

from app.types.config import Configurable, NodeConfig, get_configurable
from app.types.evidence import EvidenceSource
from app.types.messages import (
    SREMessage,
    SREMessageList,
    from_lc_message,
    make_assistant,
    make_system,
    make_tool,
    make_user,
    to_lc_messages,
)
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
    "Configurable",
    "EvidenceSource",
    "NodeConfig",
    "SREMessage",
    "SREMessageList",
    "ToolSurface",
    "RetrievalIntent",
    "RetrievalControls",
    "RetrievalControlsMap",
    "TimeBounds",
    "FilterCondition",
    "FieldSelection",
    "AggregationSpec",
    "from_lc_message",
    "get_configurable",
    "make_assistant",
    "make_system",
    "make_tool",
    "make_user",
    "to_lc_messages",
]
