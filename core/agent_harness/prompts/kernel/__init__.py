"""Shared prompt types: envelope, tiers, and surface Strategy — no agent-path knowledge."""

from __future__ import annotations

from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.kernel.surfaces import (
    PromptSurface,
    SurfaceProfile,
    profile_for,
)

__all__ = [
    "PromptBlock",
    "PromptBlockKind",
    "PromptEnvelope",
    "PromptSurface",
    "PromptTier",
    "SurfaceProfile",
    "profile_for",
]
