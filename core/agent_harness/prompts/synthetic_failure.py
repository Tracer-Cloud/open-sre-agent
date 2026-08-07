"""Grounding block for a failed ``opensre tests synthetic`` run.

Owns the whole concern: deciding whether the user is asking about the failure,
reading the saved observation off disk, and rendering the prompt block. Kept out
of ``assistant`` so prompt assembly stays assembly — this is the only prompt
input that touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from config.constants.prompts import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

if TYPE_CHECKING:
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

# Observations carry full stderr and per-gate scores, so cap what reaches the prompt.
MAX_OBSERVATION_PROMPT_CHARS = 120_000


def requests_explanation(
    message: str,
    suggested_prompt: str = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = suggested_prompt.lower().rstrip("?")
    if m.rstrip("?") == suggested:
        return True
    if "why" in m and "fail" in m:
        return True
    return "what went wrong" in m


def load_observation_text(path_str: str, *, max_chars: int = MAX_OBSERVATION_PROMPT_CHARS) -> str:
    """Read the saved observation JSON, truncated to ``max_chars`` (empty if unreadable)."""
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) > max_chars:
        return "".join(
            (
                raw[:max_chars],
                f"\n… [truncated for prompt size; observation is {len(raw)} characters total]",
            )
        )
    return raw


def build_block(
    ctx: TurnSnapshot,
    *,
    suggested_prompt: str = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
) -> str:
    """Render the observation block, or empty when this turn is not about a failure."""
    obs_path = ctx.last_synthetic_observation_path
    if not obs_path:
        return ""

    if not requests_explanation(ctx.text, suggested_prompt=suggested_prompt):
        return ""

    obs_text = load_observation_text(obs_path)
    if not obs_text:
        return ""

    return (
        "The user is asking about a failed `opensre tests synthetic` run "
        "in this checkout. The JSON below is the saved observation "
        f"(scores, gates, stderr summary). Path: {obs_path}\n"
        "Use it to explain validation failures. Do not say nothing ran or "
        "that you lack context — the run completed and this file was written.\n\n"
        f"--- observation_json ---\n{obs_text}\n\n"
    )


__all__ = [
    "MAX_OBSERVATION_PROMPT_CHARS",
    "build_block",
    "load_observation_text",
    "requests_explanation",
]
