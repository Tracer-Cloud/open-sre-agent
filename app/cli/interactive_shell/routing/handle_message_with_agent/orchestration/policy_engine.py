"""Small reusable policy-phase execution helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class MatchPhase[TContext, TResult, TTag]:
    """Policy phase that maps context to a result and may or may not match."""

    tag: TTag
    run: Callable[[TContext], TResult]


def run_first_match[TContext, TResult, TTag](
    context: TContext,
    phases: Iterable[MatchPhase[TContext, TResult, TTag]],
    *,
    is_match: Callable[[TResult], bool],
) -> tuple[TResult, TTag] | tuple[None, None]:
    """Run phases in order and return the first matching result and phase tag."""
    for phase in phases:
        result = phase.run(context)
        if is_match(result):
            return result, phase.tag
    return None, None


@dataclass(frozen=True)
class TransformPhase[TState, TTag]:
    """Policy transform phase that maps one state to another."""

    tag: TTag
    apply: Callable[[TState], TState]


def apply_transform_phases[TState, TTag](
    state: TState,
    phases: Iterable[TransformPhase[TState, TTag]],
    *,
    changed: Callable[[TState, TState], bool],
    stop_when: Callable[[TState], bool] | None = None,
) -> tuple[TState, tuple[TTag, ...]]:
    """Apply ordered transforms, collecting tags for phases that changed state."""
    applied: list[TTag] = []
    current = state
    for phase in phases:
        next_state = phase.apply(current)
        if changed(current, next_state):
            applied.append(phase.tag)
        current = next_state
        if stop_when is not None and stop_when(current):
            break
    return current, tuple(applied)


__all__ = [
    "MatchPhase",
    "TransformPhase",
    "apply_transform_phases",
    "run_first_match",
]
