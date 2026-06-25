"""Shared domain types and contracts used across orchestration, tools, and state."""

from app.core.domain.types.config import Configurable, NodeConfig, get_configurable
from app.core.domain.types.evidence import EvidenceSource
from app.core.domain.types.retrieval import (
    AggregationSpec,
    FieldSelection,
    FilterCondition,
    RetrievalControls,
    RetrievalControlsMap,
    RetrievalIntent,
    TimeBounds,
)
from app.core.domain.types.root_cause_categories import (
    GENERIC_FALLBACK_CATEGORIES,
    VALID_ROOT_CAUSE_CATEGORIES,
    RootCauseCategory,
    categories_by_group,
    render_prompt_taxonomy,
)
from app.core.domain.types.tools import ToolSurface

__all__ = [
    "AggregationSpec",
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
    "ToolSurface",
    "VALID_ROOT_CAUSE_CATEGORIES",
    "categories_by_group",
    "get_configurable",
    "render_prompt_taxonomy",
]
