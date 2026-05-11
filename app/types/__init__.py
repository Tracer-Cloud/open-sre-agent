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
from app.types.root_cause_categories import (
    GENERIC_FALLBACK_CATEGORIES,
    VALID_ROOT_CAUSE_CATEGORIES,
    RootCauseCategory,
    categories_by_group,
    render_prompt_taxonomy,
)
from app.types.tools import ToolSurface

__all__ = [
    "AggregationSpec",
    "AssistantTurn",
    "BoundChatModel",
    "Configurable",
    "EvidenceSource",
    "FieldSelection",
    "FilterCondition",
    "GENERIC_FALLBACK_CATEGORIES",
    "NodeConfig",
    "RetrievalControls",
    "RetrievalControlsMap",
    "RetrievalIntent",
    "RootCauseCategory",
    "TimeBounds",
    "ToolCallPayload",
    "ToolSurface",
    "VALID_ROOT_CAUSE_CATEGORIES",
    "categories_by_group",
    "get_configurable",
    "render_prompt_taxonomy",
]
