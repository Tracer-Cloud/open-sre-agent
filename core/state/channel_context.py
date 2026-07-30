"""Generic per-channel delivery context helpers.

Investigation/delivery state stores messenger metadata in one bag
(``channel_contexts``) keyed by channel name (``slack``, ``telegram``, …)
instead of per-vendor top-level fields on ``AgentState``.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

CHANNEL_CONTEXTS_KEY = "channel_contexts"


def get_channel_context(
    state: Mapping[str, Any],
    channel: str,
) -> dict[str, Any]:
    """Return a shallow copy of the context dict for ``channel``, or ``{}``."""
    bag = state.get(CHANNEL_CONTEXTS_KEY)
    if not isinstance(bag, dict):
        return {}
    raw = bag.get(channel)
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def set_channel_context(
    state: MutableMapping[str, Any],
    channel: str,
    context: Mapping[str, Any] | None,
) -> None:
    """Store ``context`` under ``channel`` in ``state["channel_contexts"]``."""
    bag = state.get(CHANNEL_CONTEXTS_KEY)
    if not isinstance(bag, dict):
        bag = {}
        state[CHANNEL_CONTEXTS_KEY] = bag
    if context is None:
        bag.pop(channel, None)
    else:
        bag[channel] = dict(context)


__all__ = [
    "CHANNEL_CONTEXTS_KEY",
    "get_channel_context",
    "set_channel_context",
]
