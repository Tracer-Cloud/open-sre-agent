"""Optional hooks for building a :class:`HeadlessAgent`.

``None`` on a field means the host's usual defaults. For
``apply_capability_policy``, ``None`` means do not mutate the session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BuildTools = Callable[..., Any]
BuildPrompts = Callable[..., Any]
BuildGather = Callable[..., Any]
ApplyCapabilityPolicy = Callable[[Any], None]


@dataclass(frozen=True)
class AgentBuildConfig:
    """How a host wants its headless agent built.

    Omit a field to keep that host's usual default. ``apply_capability_policy``
    left unset means do not mutate the session.
    """

    build_tools: BuildTools | None = None
    build_prompts: BuildPrompts | None = None
    build_gather: BuildGather | None = None
    error_reporter: Any | None = None
    apply_capability_policy: ApplyCapabilityPolicy | None = None


__all__ = [
    "AgentBuildConfig",
    "ApplyCapabilityPolicy",
    "BuildGather",
    "BuildPrompts",
    "BuildTools",
]
