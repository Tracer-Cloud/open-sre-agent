"""Optional hooks for building a :class:`HeadlessAgent`.

``None`` on a field means the host's usual defaults. For
``apply_capability_policy``, ``None`` means do not mutate the session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from core.agent_harness.ports import ErrorReporter, PromptContextProvider, ToolProvider
from core.agent_harness.turns.gather_phase import GatherPhase


class BuildTools(Protocol):
    """``(session, console, logger, observer) -> ToolProvider``."""

    def __call__(
        self,
        session: Any,
        console: Any,
        logger: logging.Logger,
        observer: Any,
    ) -> ToolProvider:
        """Return the tools for this session."""


class BuildPrompts(Protocol):
    """``(session) -> PromptContextProvider``."""

    def __call__(self, session: Any) -> PromptContextProvider:
        """Return the prompt context for this session."""


class BuildGather(Protocol):
    """``(session, console) -> GatherPhase``."""

    def __call__(self, session: Any, console: Any) -> GatherPhase:
        """Return how this host runs the gather phase."""


class ApplyCapabilityPolicy(Protocol):
    """``(session) -> None``. ``None`` on the config field means do not call."""

    def __call__(self, session: Any) -> None:
        """Mutate ``session`` capabilities, or do nothing."""


@dataclass(frozen=True)
class AgentBuildConfig:
    """How a host wants its headless agent built.

    Omit a field to keep that host's usual default. ``apply_capability_policy``
    left unset means do not mutate the session. Passing an empty
    ``AgentBuildConfig()`` is not the same as omitting the config: the
    gateway pool injects chat withholds only when ``agent_build is None``.
    """

    build_tools: BuildTools | None = None
    build_prompts: BuildPrompts | None = None
    build_gather: BuildGather | None = None
    error_reporter: ErrorReporter | None = None
    apply_capability_policy: ApplyCapabilityPolicy | None = None


__all__ = [
    "AgentBuildConfig",
    "ApplyCapabilityPolicy",
    "BuildGather",
    "BuildPrompts",
    "BuildTools",
]
