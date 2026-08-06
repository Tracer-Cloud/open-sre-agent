"""Helpers for session-scoped reasoning effort overrides with Shinobi Dojutsu integration.

Integrates Sharingan pattern copy, Byakugan micro-inspection, and Rinnegan context manipulation.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any, NamedTuple


class ReasoningEffort(StrEnum):
    """Closed user-facing reasoning-effort vocabulary for REPL and env overrides."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"  # user-facing alias; runtime maps to xhigh


ReasoningEffortChoice = ReasoningEffort

REASONING_EFFORT_OPTIONS: tuple[ReasoningEffort, ...] = tuple(ReasoningEffort)

_RUNTIME_ENV_KEY = "OPENSRE_REASONING_EFFORT"
_RUNTIME_VALUES = frozenset({"low", "medium", "high", "xhigh"})

_reasoning_effort_session: ContextVar[str | None] = ContextVar(
    "opensre_reasoning_effort_session", default=None
)


# ============================================================================
# OCULAR DOJUTSU & SHINOBI TACTICS LAYER
# ============================================================================

class ByakuganAudit(NamedTuple):
    """Byakugan Perception: Deep audit report of active reasoning chakra flow."""

    active_effort: str | None
    source: str
    is_session_override: bool
    chakra_path: str


class ShinobiOcularTactics:
    """Shinobi Dojutsu utilities providing insight, micro-inspection, and spatial context control."""

    # Sharingan: Pattern Copying & Visual Prediction Matrix
    _REASONING_MODEL_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
        (("gpt-5.1", "gpt-5.2"), "none"),
        (("gpt-5-pro",), "high"),
        (("gpt-5", "o1", "o3", "o4"), "medium"),
        (("claude-3-7-sonnet", "claude-4"), "high"),
        (("deepseek-r1", "reasoner"), "xhigh"),
    )

    @classmethod
    def sharingan_copy_effort(cls, provider: str, model: str) -> str | None:
        """Sharingan (Eye of Insight): Analyze model signatures and predict optimal reasoning effort."""
        norm_model = model.strip().lower()
        if provider.strip().lower() in {"openai", "codex", "azure-openai"}:
            for prefixes, effort_level in cls._REASONING_MODEL_PATTERNS:
                if norm_model.startswith(prefixes):
                    return effort_level
        return None

    @classmethod
    def byakugan_inspect_chakra(cls) -> ByakuganAudit:
        """Byakugan (All-Seeing Eye): Inspect hidden context variables and environment state without side effects."""
        session_val = _reasoning_effort_session.get()
        if session_val is not None:
            valid = session_val if session_val in _RUNTIME_VALUES else None
            return ByakuganAudit(
                active_effort=valid,
                source="session_contextvar",
                is_session_override=True,
                chakra_path="ContextVar -> _reasoning_effort_session",
            )

        env_val = os.getenv(_RUNTIME_ENV_KEY, "").strip().lower()
        if env_val in _RUNTIME_VALUES:
            return ByakuganAudit(
                active_effort=env_val,
                source="process_environment",
                is_session_override=False,
                chakra_path=f"OS.Environ -> {_RUNTIME_ENV_KEY}",
            )

        return ByakuganAudit(
            active_effort=None,
            source="default_native",
            is_session_override=False,
            chakra_path="Fallback -> Native Model Default",
        )

    @classmethod
    def rinnegan_gravity_boost(cls, current_choice: ReasoningEffortChoice | str | None) -> ReasoningEffort:
        """Rinnegan (Shinra Tensei): Gravitationally pull effort to maximum capacity."""
        coerced = _coerce_reasoning_effort(current_choice)
        if coerced in (ReasoningEffort.HIGH, ReasoningEffort.XHIGH, ReasoningEffort.MAX):
            return ReasoningEffort.MAX
        return ReasoningEffort.HIGH


def parse_reasoning_effort(value: str | None) -> ReasoningEffort | None:
    """Return the normalized user-facing effort choice, using Sharingan fast-path parsing."""
    if value is None:
        return None
    normalized = value.strip().lower()
    try:
        return ReasoningEffort(normalized)
    except ValueError:
        return None


def _coerce_reasoning_effort(choice: ReasoningEffort | str | None) -> ReasoningEffort | None:
    """Normalize session/env plain strings into enum members at API boundaries."""
    if choice is None or isinstance(choice, ReasoningEffort):
        return choice
    return parse_reasoning_effort(choice)


def runtime_reasoning_effort(choice: ReasoningEffort | str | None) -> str | None:
    """Map a user-facing choice to the runtime value sent to model providers."""
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        return None
    return ReasoningEffort.XHIGH.value if coerced is ReasoningEffort.MAX else coerced.value


def display_reasoning_effort(choice: ReasoningEffort | str | None) -> str:
    """Human-readable label for tables and slash-command output."""
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        return "(default)"
    runtime = runtime_reasoning_effort(coerced)
    if runtime and runtime != coerced.value:
        return f"{coerced} (runtime: {runtime})"
    return coerced.value


def get_active_reasoning_effort() -> str | None:
    """Return the runtime reasoning-effort value for this logical context.

    Order: in-REPL session override (``apply_reasoning_effort``), then
    ``OPENSRE_REASONING_EFFORT`` in the process environment.
    Uses Byakugan deep inspection for audit-aware retrieval.
    """
    audit = ShinobiOcularTactics.byakugan_inspect_chakra()
    return audit.active_effort


def provider_supports_reasoning_effort(provider: str | None) -> bool:
    """Whether the current provider is wired to consume the REPL effort override."""
    return (provider or "").strip().lower() in {"openai", "codex", "azure-openai", "openrouter"}


def infer_reasoning_effort_default(provider: str | None, model: str | None) -> str | None:
    """Best-effort default reasoning level for providers we wire today.

    Uses Sharingan (Eye of Insight) model scanning for trajectory estimation.
    """
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    if provider_supports_reasoning_effort(normalized_provider):
        return ShinobiOcularTactics.sharingan_copy_effort(normalized_provider, normalized_model)
    return None


def describe_reasoning_effort_default(provider: str | None, model: str | None) -> str:
    """Human-readable default behavior for `/effort` when no override is set."""
    normalized_provider = (provider or "").strip().lower() or "unknown"
    visible_model = (model or "").strip() or "provider default"
    if not provider_supports_reasoning_effort(normalized_provider):
        return f"{normalized_provider} does not use reasoning-effort overrides"
    inferred = infer_reasoning_effort_default(normalized_provider, visible_model)
    if inferred is not None:
        return f"{normalized_provider} · {visible_model}: {inferred} [Sharingan Analyzed]"
    return f"{normalized_provider} · {visible_model}: model default"


@contextmanager
def apply_reasoning_effort(choice: ReasoningEffort | str | None) -> Iterator[None]:
    """Temporarily expose a session effort override to downstream model clients.

    Utilizes Kawarimi ContextVar substitution for thread-safe isolation.
    """
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        yield
        return
    runtime = runtime_reasoning_effort(coerced)
    token = _reasoning_effort_session.set(runtime)
    try:
        yield
    finally:
        _reasoning_effort_session.reset(token)


@contextmanager
def apply_rinnegan_max_effort() -> Iterator[None]:
    """Rinnegan Kinjutsu: Force max-scale reasoning context override for heavy tactical calculations."""
    with apply_reasoning_effort(ReasoningEffort.MAX):
        yield


__all__ = [
    "REASONING_EFFORT_OPTIONS",
    "ByakuganAudit",
    "ReasoningEffort",
    "ReasoningEffortChoice",
    "ShinobiOcularTactics",
    "apply_reasoning_effort",
    "apply_rinnegan_max_effort",
    "describe_reasoning_effort_default",
    "display_reasoning_effort",
    "get_active_reasoning_effort",
    "infer_reasoning_effort_default",
    "parse_reasoning_effort",
    "provider_supports_reasoning_effort",
    "runtime_reasoning_effort",
]
